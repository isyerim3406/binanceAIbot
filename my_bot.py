import asyncio
import os
import time
from datetime import datetime
from binance import AsyncClient, BinanceSocketManager
from dotenv import load_dotenv
import telegram
from telegram import constants
from aiohttp import ClientSession
import json
import requests

# .env dosyasını yükle
load_dotenv()

# =========================================================================================
# BOT AYARLARI
# =========================================================================================
CFG = {
    'SYMBOL': os.getenv('SYMBOL', 'ETHUSDT'),
    'INTERVAL': os.getenv('INTERVAL', '1m'),
    'MACD_FAST_PERIOD': int(os.getenv('MACD_FAST_PERIOD', 12)),
    'MACD_SLOW_PERIOD': int(os.getenv('MACD_SLOW_PERIOD', 26)),
    'MACD_SIGNAL_PERIOD': int(os.getenv('MACD_SIGNAL_PERIOD', 9)),
    'COOLDOWN_SECONDS': int(os.getenv('COOLDOWN_SECONDS', 10)),
    'BOT_NAME': os.getenv('BOT_NAME', 'Binance MACD Botu'),
    'MODE': os.getenv('MODE', 'Simülasyon'),
    'HISTORICAL_DATA_COUNT': int(os.getenv('HISTORICAL_DATA_COUNT', 1000))
}

# =========================================================================================
# TELEGRAM VE API AYARLARI
# =========================================================================================
telegram_bot = telegram.Bot(token=os.getenv('TG_TOKEN')) if os.getenv('TG_TOKEN') else None
TELEGRAM_CHAT_ID = os.getenv('TG_CHAT_ID')

# Hugging Face API ayarları
HF_API_TOKEN = os.getenv('HF_API_TOKEN')
HF_API_URL = "https://api-inference.huggingface.co/models/distilbert-base-uncased-finetuned-sst-2-english"
HF_HEADERS = {"Authorization": f"Bearer {HF_API_TOKEN}"}

# =========================================================================================
# STRATEJİ VE POZİSYON YÖNETİMİ
# =========================================================================================
class MACDStrategy:
    def __init__(self):
        self.closes = []
        self.ema_12, self.ema_26 = None, None
        self.macd_values = []
        self.signal_line = None
        self.prev_macd_line = None
        self.prev_signal_line = None
        self.is_initialized = False

    def calculate_ema(self, value, period, previous_ema=None):
        if previous_ema is None:
            return value
        alpha = 2 / (period + 1)
        return alpha * value + (1 - alpha) * previous_ema

    def process_candle(self, close_price):
        self.closes.append(close_price)
        
        if len(self.closes) < max(CFG['MACD_FAST_PERIOD'], CFG['MACD_SLOW_PERIOD']):
            return None, None, None
        
        self.ema_12 = self.calculate_ema(close_price, CFG['MACD_FAST_PERIOD'], self.ema_12)
        self.ema_26 = self.calculate_ema(close_price, CFG['MACD_SLOW_PERIOD'], self.ema_26)

        macd_line = self.ema_12 - self.ema_26
        self.macd_values.append(macd_line)
        
        self.signal_line = self.calculate_ema(macd_line, CFG['MACD_SIGNAL_PERIOD'], self.signal_line)

        histogram = macd_line - self.signal_line
        
        return macd_line, self.signal_line, histogram

    def get_signal(self, macd_line, signal_line):
        if not self.is_initialized:
            self.prev_macd_line = macd_line
            self.prev_signal_line = signal_line
            self.is_initialized = True
            return None
        
        signal = None
        if self.prev_macd_line < self.prev_signal_line and macd_line > signal_line:
            signal = "AL"
        elif self.prev_macd_line > self.prev_signal_line and macd_line < signal_line:
            signal = "SAT"
            
        self.prev_macd_line = macd_line
        self.prev_signal_line = signal_line
        
        return signal

# =========================================================================================
# API VE İLETİŞİM FONKSİYONLARI
# =========================================================================================

async def send_telegram_message(message):
    if telegram_bot and TELEGRAM_CHAT_ID:
        try:
            await telegram_bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=message,
                parse_mode=constants.ParseMode.HTML
            )
            print(f"Telegram'a gönderildi: {message}")
        except Exception as e:
            print(f"Telegram mesajı gönderilirken hata oluştu: {e}")

def query_hugging_face(payload):
    """
    Hugging Face Inference API'sine istek gönderen fonksiyon.
    Payload, API'nin beklentisine uygun olarak biçimlendirilmiş bir sözlük olmalıdır.
    """
    try:
        response = requests.post(HF_API_URL, headers=HF_HEADERS, json=payload)
        response.raise_for_status() # HTTP hatalarını kontrol et
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Hugging Face API sorgusunda hata: {e}")
        return None

# =========================================================================================
# ANA BOT MANTIĞI
# =========================================================================================

async def main():
    print("Bot başlatılıyor...")
    if not CFG['SYMBOL']:
        print("SYMBOL ayarı belirtilmedi. Lütfen .env dosyasını kontrol edin.")
        return

    client = await AsyncClient.create(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_SECRET_KEY'))
    bm = BinanceSocketManager(client)
    strategy = MACDStrategy()
    last_signal_time = 0

    # Geçmiş veriyi çek
    try:
        historical_klines = await client.get_klines(symbol=CFG['SYMBOL'], interval=CFG['INTERVAL'], limit=CFG['HISTORICAL_DATA_COUNT'])
        for kline in historical_klines:
            close_price = float(kline[4])
            strategy.process_candle(close_price)
    except Exception as e:
        print(f"Geçmiş veri çekilirken hata: {e}")
        return

    # WebSocket ile anlık veri akışı
    async with bm.kline_socket(symbol=CFG['SYMBOL'], interval=CFG['INTERVAL']) as ts:
        print(f"WebSocket bağlantısı kuruldu: {CFG['SYMBOL']} {CFG['INTERVAL']}")
        while True:
            res = await ts.recv()
            if res['e'] == 'kline':
                kline = res['k']
                if kline['x']:  # Mum çubuğu kapandı
                    close_price = float(kline['c'])
                    macd_line, signal_line, histogram = strategy.process_candle(close_price)
                    
                    if macd_line is not None and signal_line is not None:
                        signal = strategy.get_signal(macd_line, signal_line)

                        current_time = time.time()
                        if signal and (current_time - last_signal_time > CFG['COOLDOWN_SECONDS']):
                            timestamp = datetime.fromtimestamp(kline['T'] / 1000).strftime('%Y-%m-%d %H:%M:%S')
                            message = f"""
                            <b>{CFG['BOT_NAME']} - {CFG['MODE']} Modu</b>
                            ------------------------------------
                            <b>Sinyal:</b> {signal}
                            <b>Sembol:</b> {CFG['SYMBOL']}
                            <b>Mum:</b> {CFG['INTERVAL']}
                            <b>Kapanış Fiyatı:</b> {close_price:.2f}
                            <b>Zaman:</b> {timestamp}
                            """
                            print(message)
                            await send_telegram_message(message)
                            last_signal_time = current_time

                            # Hugging Face ile metin analizi yapma örneği
                            try:
                                sentiment_data = query_hugging_face({"inputs": f"Bir {signal} sinyali geldi. Fiyat: {close_price}"})
                                if sentiment_data and sentiment_data[0]:
                                    sentiment_label = sentiment_data[0][0]['label']
                                    sentiment_score = sentiment_data[0][0]['score']
                                    sentiment_message = f"<b>Duygu Analizi:</b> {sentiment_label} (Kesinlik: {sentiment_score:.2f})"
                                    await send_telegram_message(sentiment_message)
                            except Exception as e:
                                print(f"Duygu analizi gönderilirken hata oluştu: {e}")

            await asyncio.sleep(1) # CPU kullanımını azaltmak için bekle

if __name__ == "__main__":
    asyncio.run(main())
