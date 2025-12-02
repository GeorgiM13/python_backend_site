from flask import Flask, jsonify, request
from flask_cors import CORS
import pandas as pd
from prophet import Prophet
from supabase import create_client, Client
from dotenv import load_dotenv
import os
import numpy as np
from datetime import datetime

load_dotenv()
app = Flask(__name__)
CORS(app)

SUPABASE_URL = os.getenv("VITE_SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ ГРЕШКА: Липсват ключове в .env!")
    exit()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def remove_outliers_iqr(df, column_name):
    Q1 = df[column_name].quantile(0.25)
    Q3 = df[column_name].quantile(0.75)
    IQR = Q3 - Q1
    
    upper_bound = Q3 + 1.5 * IQR
    
    
    print(f"🧹 Smart Cleaning: Q1={Q1:.2f}, Q3={Q3:.2f}, Upper Limit={upper_bound:.2f}")
    
    return df[df[column_name] <= upper_bound]

@app.route('/predict', methods=['GET'])
def predict():
    building_id = request.args.get('building_id')
    
    if not building_id or building_id == 'all':
        return jsonify({"error": "Моля изберете сграда."}), 400

    print(f"🤖 AI Анализ за сграда: {building_id}...")

    try:
        response = supabase.table('expenses') \
            .select("year, month, current_month, type") \
            .eq('building_id', building_id) \
            .execute()
        data = response.data
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not data:
        return jsonify({"error": "Няма данни."}), 404

    df = pd.DataFrame(data)
    df['ds'] = pd.to_datetime(df['year'].astype(str) + '-' + df['month'].astype(str) + '-01')
    
    current_date = datetime.now()
    df = df[df['ds'] <= current_date]

    if df.empty:
        return jsonify({"error": "Няма исторически данни."}), 404

    df_history_full = df.groupby('ds')['current_month'].sum().reset_index()
    df_history_full.columns = ['ds', 'y']

    df_training = df_history_full.copy()
    
    median_val = df_training['y'].median()
    threshold = median_val * 3.0  
    if threshold < 600: threshold = 600
    
    df_training['y'] = df_training.apply(
        lambda row: median_val if row['y'] > threshold else row['y'], 
        axis=1
    )

    if len(df_training) > 4:
        df_training['y'] = df_training['y'].rolling(window=3, min_periods=1, center=True).mean()

    is_prophet_model = False

    forecast_periods = 12 

    if len(df_training) < 5:
        method = "Statistical Average (Smoothed)"
        avg_value = df_training['y'].mean()
        
        last_date = df_history_full['ds'].max()
        future_dates = pd.date_range(start=last_date, periods=forecast_periods + 1, freq='MS')[1:]
        
        past_dates = df_training['ds'].tolist()
        full_forecast_data = [{'ds': d, 'yhat': avg_value} for d in past_dates] + \
                             [{'ds': d, 'yhat': avg_value} for d in future_dates]
        
        result = pd.DataFrame(full_forecast_data)
        
    else:
        print(f"🚀 Prophet AI Active. Training on SMOOTHED data...")
        method = "Prophet AI (12 Months + Full History)"
        is_prophet_model = True
        
        m = Prophet(
            daily_seasonality=False, 
            weekly_seasonality=False,
            seasonality_mode='additive',
            changepoint_prior_scale=0.05, 
            seasonality_prior_scale=0.01 
        )
        m.add_seasonality(name='monthly_pattern', period=30.5, fourier_order=1, prior_scale=0.1)
        
        try:
            m.fit(df_training)
            
            future = m.make_future_dataframe(periods=forecast_periods, freq='M')
            
            forecast = m.predict(future)
            result = forecast.copy()
        except Exception as e:
            print("Error fitting Prophet:", e)
            method = "Fallback"
            avg_value = df_training['y'].mean()
            result = pd.DataFrame([{'ds': df_history_full['ds'].max(), 'yhat': avg_value}])

    result['date'] = result['ds'].dt.strftime('%Y-%m')
    df_history_full['date'] = df_history_full['ds'].dt.strftime('%Y-%m')

    all_dates = sorted(list(set(df_history_full['date'].tolist() + result['date'].tolist())))
    
    combined_data = []
    
    for date in all_dates:
        real_row = df_history_full[df_history_full['date'] == date]
        actual = round(real_row['y'].values[0], 2) if not real_row.empty else None
        
        forecast_row = result[result['date'] == date]
        
        prediction = None
        trend = None

        if not forecast_row.empty:
            prediction = round(forecast_row['yhat'].values[0], 2)
            if is_prophet_model and 'trend' in forecast_row:
                trend = round(forecast_row['trend'].values[0], 2)
        
        if prediction and prediction < 0: prediction = 0

        
        combined_data.append({
            "date": date,
            "actual": actual,
            "forecast": prediction,
            "trend": trend
        })

    return jsonify({ "method": method, "data": combined_data })

if __name__ == '__main__':
    print("🚀 AI Сървърът е готов!")
    app.run(port=5000, debug=True)