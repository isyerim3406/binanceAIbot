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

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-05-20:generateContent?key={GEMINI_API_KEY}"

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

    def calculate_ema(self, value, period, previous_ema=None):
        if previous_ema is None:
            return value
        alpha = 2 / (period + 1)
        return alpha * value + (1 - alpha) * previous_ema

    def process_candle(self, close_price):
        self.closes.append(close_price)
        
        # EMA hesaplamaları için yeterli veri kontrolü
        if len(self.closes) < max(CFG['MACD_FAST_PERIOD'], CFG['MACD_SLOW_PERIOD']):
            return None, None, None
        
        # MACD Hatlarını Hesapla
        self.ema_12 = self.calculate_ema(close_price, CFG['MACD_FAST_PERIOD'], self.ema_12)
        self.ema_26 = self.calculate_ema(close_price, CFG['MACD_SLOW_PERIOD'], self.ema_26)

        # MACD Hattı
        macd_line = self.ema_12 - self.ema_26
        self.macd_values.append(macd_line)
        
        # Sinyal Hattı
        self.signal_line = self.calculate_ema(macd_line, CFG['MACD_SIGNAL_PERIOD'], self.signal_line)

        # Histogram
        histogram = macd_line - self.signal_line
        
        return macd_line, self.signal_line, histogram

    def get_signal(self, macd_line, signal_line):
        if macd_line is None or signal_line is None or self.prev_macd_line is None or self.prev_signal_line is None:
            # Önceki değerleri güncelle
            self.prev_macd_line = macd_line
            self.prev_signal_line = signal_line
            return None
        
        signal = None
        if self.prev_macd_line < self.prev_signal_line and macd_line > signal_line:
            signal = "AL"
        elif self.prev_macd_line > self.prev_signal_line and mac
