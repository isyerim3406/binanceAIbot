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
    'MODE': os.getenv('MODE', 'Simülasyon')
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
        elif self.prev_macd_line > self.prev_signal_line and macd_line < signal_line:
            signal = "SAT"
        
        # Önceki değerleri güncelle
        self.prev_macd_line = macd_line
        self.prev_signal_line = signal_line
        
        return signal

# =========================================================================================
# TELEGRAM VE GEMINI ENTEGRASYONU
# =========================================================================================
async def send_telegram_message(text, parse_mode=constants.ParseMode.MARKDOWN):
    if not telegram_bot or not TELEGRAM_CHAT_ID:
        print("Telegram ayarları eksik. Mesaj gönderilemedi.")
        return
    try:
        await telegram_bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=text,
            parse_mode=parse_mode
        )
    except Exception as e:
        print(f"Telegram'a mesaj gönderilirken hata oluştu: {e}")

async def get_gemini_market_insight(symbol, signal_type):
    if not GEMINI_API_KEY:
        print("Gemini API anahtarı ayarlanmamış.")
        return {"verdict": "neutral", "analysis": "API anahtarı eksik, analiz yapılamadı."}

    user_query = f"""
    You are a financial analyst. Based on a '{signal_type}' signal for {symbol} from a MACD strategy, provide a very short analysis of the market state.
    Provide your response in JSON format with two keys:
    1. 'verdict': A single word string. Use 'bullish' for a positive signal, 'bearish' for a negative signal, or 'neutral' if there is no strong conviction.
    2. 'analysis': A short paragraph explaining your verdict based on real-time market data.
    """

    headers = {
        'Content-Type': 'application/json',
    }
    payload = {
        "contents": [{"parts": [{"text": user_query}]}],
        "tools": [{"google_search": {}}],
        "systemInstruction": {"parts": [{"text": "You are a world-class financial analyst. Provide a concise analysis."}]},
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "verdict": {"type": "STRING"},
                    "analysis": {"type": "STRING"}
                }
            }
        }
    }
    
    async with ClientSession() as session:
        try:
            async with session.post(GEMINI_API_URL, headers=headers, json=payload) as response:
                result = await response.json()
                text_content = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '{}')
                parsed_json = json.loads(text_content)
                return parsed_json
        except Exception as e:
            print(f"Gemini API'ye bağlanırken hata oluştu: {e}")
            return {"verdict": "neutral", "analysis": "Gemini analizinde hata oluştu."}

# =========================================================================================
# ANA BOT DÖNGÜSÜ
# =========================================================================================
async def run_bot():
    last_signal_time = 0.0
    strategy = MACDStrategy()
    
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Bot başlatılıyor...")
    await send_telegram_message(f"**{CFG['BOT_NAME']} Başladı!**\n\nSembol: {CFG['SYMBOL']}\nZaman Aralığı: {CFG['INTERVAL']}\nMod: {CFG['MODE']}")
    
    client = await AsyncClient.create()
    bm = BinanceSocketManager(client)
    ts = bm.kline_socket(symbol=CFG['SYMBOL'], interval=CFG['INTERVAL'])

    async with ts as stream:
        while True:
            msg = await stream.recv()
            if msg.get('e') != 'kline':
                continue
            
            k = msg['k']
            close_price = float(k['c'])
            
            # Bu log her yeni fiyat güncellemesinde çalışır, mum kapalı olmasa bile.
            print(f"Fiyat güncellendi: {close_price}")

            if k['x']: # Mum kapalıysa
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Yeni bar alındı. Fiyat: {close_price}")

                # Mum tamamlandığında stratejiyi çalıştır
                macd_line, signal_line, histogram = strategy.process_candle(close_price)
                signal = strategy.get_signal(macd_line, signal_line)

                if signal:
                    now = time.time()
                    if (now - last_signal_time) < CFG['COOLDOWN_SECONDS']:
                        continue
                    last_signal_time = now

                    # Gemini API'den analiz ve karar alma
                    gemini_insight = await get_gemini_market_insight(CFG['SYMBOL'], signal)
                    
                    # Gemini'nin kararını kontrol et
                    # Eğer AL sinyali ve "bullish" veya SAT sinyali ve "bearish" ise devam et
                    # Aksi halde, işlemi atla
                    should_trade = False
                    if signal == "AL" and gemini_insight.get('verdict') == "bullish":
                        should_trade = True
                    elif signal == "SAT" and gemini_insight.get('verdict') == "bearish":
                        should_trade = True

                    if not should_trade:
                        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Gemini analizi sinyali desteklemiyor. İşlem iptal edildi. Karar: {gemini_insight.get('verdict')}")
                        message = (
                            f"**Sinyal İptal Edildi!**\n\n"
                            f"Bot Adı: {CFG['BOT_NAME']}\n"
                            f"Sembol: {CFG['SYMBOL']}\n"
                            f"Oluşan Sinyal: {signal}\n"
                            f"**Gemini Kararı: {gemini_insight.get('verdict')}**\n\n"
                            f"Analiz: {gemini_insight.get('analysis')}"
                        )
                        await send_telegram_message(message)
                        continue

                    # İşlem için her şey uygunsa
                    message = (
                        f"**{signal} Sinyali!**\n\n"
                        f"Bot Adı: {CFG['BOT_NAME']}\n"
                        f"Sembol: {CFG['SYMBOL']}\n"
                        f"Zaman Aralığı: {CFG['INTERVAL']}\n"
                        f"Fiyat: {close_price}\n"
                        f"MACD: {macd_line:.4f}\n"
                        f"Sinyal Hattı: {signal_line:.4f}\n"
                        f"Histogram: {histogram:.4f}\n\n"
                        f"**Gemini Kararı: {gemini_insight.get('verdict')}**\n"
                        f"**Piyasa Analizi:**\n{gemini_insight.get('analysis')}"
                    )
                    await send_telegram_message(message)

    await client.close_connection()

if __name__ == "__main__":
    asyncio.run(run_bot())
