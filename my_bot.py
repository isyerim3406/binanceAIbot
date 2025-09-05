import os
import requests
import json
import time
import pandas as pd
from binance.client import Client
from telegram import Bot

# Bu betik, MACD stratejisine göre alım/satım sinyalleri üretir ve bunları tradingview_bridge.py'ye gönderir.
# Binance API anahtarları boş bırakılırsa, bot simülasyon modunda çalışır ve gerçek işlem yapmaz.
# Ayrıca ürettiği sinyalleri Telegram'a bildirim olarak gönderir.

# =========================================================================================
# API VE AYARLAR
# =========================================================================================
# Binance API anahtarları (sadece veri çekmek için gerekli, işlem yapmak için değil)
BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY')
BINANCE_SECRET = os.environ.get('BINANCE_SECRET')

# Telegram Bot token'ı ve sohbet kimliği (chat ID)
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

# Botun gerçek modda mı yoksa simülasyon modunda mı olduğunu kontrol eder
SIMULATION_MODE = not BINANCE_API_KEY or not BINANCE_SECRET
print(f"Bot {'SIMÜLASYON' if SIMULATION_MODE else 'GERÇEK'} modda çalışıyor.")

# Webhook URL'si: tradingview_bridge.py dosyasının çalıştığı adres ve port.
WEBHOOK_URL = "http://localhost:5000/webhook"
# Sinyalleri doğrulamak için gizli anahtar
SECRET_KEY = "YOUR_STRONG_SECRET_KEY"

# Bot ayarları
SYMBOL = 'BTCUSDT'
INTERVAL = Client.KLINE_INTERVAL_1HOUR  # 1 saatlik veriler
QUANTITY = 0.001  # İşlem miktarı
RSI_PERIOD = 14
MACD_FAST_PERIOD = 12
MACD_SLOW_PERIOD = 26
MACD_SIGNAL_PERIOD = 9

# =========================================================================================
# TEKNİK ANALİZ FONKSİYONLARI
# =========================================================================================
def calculate_macd(df, fast_period, slow_period, signal_period):
    """MACD ve sinyal çizgisi hesaplaması."""
    df['ema_fast'] = df['close'].ewm(span=fast_period, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=slow_period, adjust=False).mean()
    df['macd'] = df['ema_fast'] - df['ema_slow']
    df['macd_signal'] = df['macd'].ewm(span=signal_period, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']
    return df

# =========================================================================================
# SİNYAL GÖNDERME FONKSİYONLARI
# =========================================================================================
def send_telegram_message(message: str):
    """Telegram'a mesaj gönderir."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram API bilgileri eksik. Mesaj gönderilemedi.")
        return
    try:
        telegram_bot = Bot(token=TELEGRAM_BOT_TOKEN)
        telegram_bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
        print("Telegram'a bildirim başarıyla gönderildi.")
    except Exception as e:
        print(f"Telegram'a mesaj gönderme sırasında bir hata oluştu: {e}")

def send_signal_to_bridge(symbol: str, signal: str, quantity: float):
    """
    Belirtilen parametrelerle web sunucusuna bir sinyal gönderir.
    """
    payload = {
        "secret": SECRET_KEY,
        "symbol": symbol,
        "signal": signal,
        "quantity": quantity
    }
    headers = {'Content-Type': 'application/json'}

    print(f"Sinyal gönderiliyor: {symbol} için {signal}")
    if SIMULATION_MODE:
        message = f"Simülasyon Modu: {symbol} için {signal} sinyali üretildi. Gerçek işlem yapılmadı."
        print(message)
        send_telegram_message(message)
        return

    try:
        response = requests.post(WEBHOOK_URL, data=json.dumps(payload), headers=headers)
        response.raise_for_status()
        result = response.json()
        print("Sinyal başarıyla gönderildi. Sunucu yanıtı:", result)
        
        # Başarılı sipariş sonrası Telegram'a bildirim gönder
        message = f"✅ İşlem Başarılı!\nSembol: {symbol}\nSinyal: {signal}\nMiktar: {quantity}"
        send_telegram_message(message)

    except requests.exceptions.RequestException as e:
        print(f"Sinyal gönderme sırasında hata: {e}")
        message = f"❌ İşlem Hatası!\nSembol: {symbol}\nSinyal: {signal}\nHata: {e}"
        send_telegram_message(message)

# =========================================================================================
# ANA ÇALIŞMA DÖNGÜSÜ
# =========================================================================================
def run_bot():
    """Botun ana çalışma döngüsü."""
    client = Client(BINANCE_API_KEY, BINANCE_SECRET)
    
    last_signal = None  # Son sinyali takip etmek için değişken
    
    while True:
        try:
            # Binance'dan son 500 mum çubuğu verisini çek
            klines = client.get_klines(symbol=SYMBOL, interval=INTERVAL, limit=500)
            df = pd.DataFrame(klines, columns=['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
            
            # Kapanış fiyatlarını float'a dönüştür
            df['close'] = pd.to_numeric(df['close'])
            
            # MACD'yi hesapla
            df = calculate_macd(df, MACD_FAST_PERIOD, MACD_SLOW_PERIOD, MACD_SIGNAL_PERIOD)
            
            # Son iki MACD ve sinyal değerini al
            last_macd = df['macd'].iloc[-1]
            last_macd_signal = df['macd_signal'].iloc[-1]
            prev_macd = df['macd'].iloc[-2]
            prev_macd_signal = df['macd_signal'].iloc[-2]
            
            # MACD stratejisi:
            # MACD sinyal çizgisini yukarı keserse AL
            if prev_macd < prev_macd_signal and last_macd > last_macd_signal:
                if last_signal != 'BUY':
                    send_signal_to_bridge(SYMBOL, 'BUY', QUANTITY)
                    last_signal = 'BUY'
            
            # MACD sinyal çizgisini aşağı keserse SAT
            elif prev_macd > prev_macd_signal and last_macd < last_macd_signal:
                if last_signal != 'SELL':
                    send_signal_to_bridge(SYMBOL, 'SELL', QUANTITY)
                    last_signal = 'SELL'
            
            else:
                print("Yeni sinyal yok. Bekleniyor...")

            # Her 1 saatte bir tekrarla (veriler 1 saatlik olduğu için)
            time.sleep(3600)

        except Exception as e:
            print(f"Bot döngüsü sırasında bir hata oluştu: {e}")
            time.sleep(60) # Hata durumunda 1 dakika bekle ve tekrar dene

if __name__ == '__main__':
    run_bot()
