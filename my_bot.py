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

async def get_gemini_market_condition(klines):
    if not GEMINI_API_KEY:
        print("Gemini API anahtarı ayarlanmamış.")
        return {"verdict": "trending", "analysis": "API anahtarı eksik, analiz yapılamadı."}

    user_query = f"""
    You are a market analyst. Analyze the following list of historical price data (close prices). 
    Determine if the market is currently in a 'sideways' (range-bound, low volatility) or 'trending' (clear upward or downward movement) state.
    
    Price Data: {klines}

    Provide your response in JSON format with two keys:
    1. 'verdict': A single word string. Use 'sideways' if the market is range-bound or 'trending' if there is a clear directional movement.
    2. 'analysis': A short paragraph explaining your verdict based on the provided data.
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
            async with session.post(GEMINI_API_URL, headers=headers, json=payload, timeout=60) as response:
                response.raise_for_status() # HTTP hatalarını yakala
                result = await response.json()
                
                candidate = result.get('candidates', [None])[0]
                if not candidate:
                    print("Gemini API'den geçerli aday (candidate) bulunamadı.")
                    return {"verdict": "trending", "analysis": "Geçersiz Gemini API yanıtı."}

                text_content = candidate.get('content', {}).get('parts', [{}])[0].get('text', '{}')
                parsed_json = json.loads(text_content)
                
                # Gelen verinin beklenen tipte olup olmadığını kontrol et
                verdict = parsed_json.get('verdict')
                analysis = parsed_json.get('analysis')
                if not isinstance(verdict, str) or not isinstance(analysis, str):
                    print("Gemini API yanıtındaki 'verdict' veya 'analysis' anahtarları string değil.")
                    return {"verdict": "trending", "analysis": "Gemini yanıtı geçerli formatta değil."}

                return parsed_json
        except json.JSONDecodeError:
            print("Gemini API yanıtı geçerli JSON değil.")
            return {"verdict": "trending", "analysis": "Gemini yanıtı JSON olarak çözümlenemedi."}
        except Exception as e:
            print(f"Gemini API'ye bağlanırken hata oluştu: {e}")
            return {"verdict": "trending", "analysis": "Gemini analizinde hata oluştu. Varsayılan olarak trend durumu kabul edildi."}

async def get_historical_klines(client, symbol, interval, limit):
    try:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {limit} adet geçmiş mum verisi alınıyor...")
        klines = await client.get_klines(symbol=symbol, interval=interval, limit=limit)
        return [float(k[4]) for k in klines] # Sadece kapanış fiyatlarını al
    except Exception as e:
        print(f"Geçmiş mum verisi alınırken hata oluştu: {e}")
        return None

# =========================================================================================
# ANA BOT DÖNGÜSÜ
# =========================================================================================
async def run_bot():
    last_signal_time = 0.0
    strategy = MACDStrategy()
    
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Bot başlatılıyor...")
    await send_telegram_message(f"**{CFG['BOT_NAME']} Başladı!**\n\nSembol: {CFG['SYMBOL']}\nZaman Aralığı: {CFG['INTERVAL']}\nMod: {CFG['MODE']}")
    
    client = await AsyncClient.create()
    
    # Bot başlatılırken geçmiş veriyi al ve Gemini'ye gönder
    historical_klines = await get_historical_klines(client, CFG['SYMBOL'], CFG['INTERVAL'], CFG['HISTORICAL_DATA_COUNT'])
    
    # Geçmiş veri alınamazsa botu başlatma
    if not historical_klines:
        error_message = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Geçmiş veri alınamadı, bot başlatılamıyor."
        print(error_message)
        await send_telegram_message(error_message)
        await client.close_connection()
        return

    # Gemini'den piyasa durumu analizi al
    gemini_market_state = await get_gemini_market_condition(historical_klines)
    
    # Gelen yanıtın mutlaka bir sözlük olduğunu garanti altına al
    if not isinstance(gemini_market_state, dict):
        print("Gemini'den gelen yanıt sözlük değil. Varsayılan değerler kullanılıyor.")
        gemini_market_state = {"verdict": "trending", "analysis": "Geçersiz yanıt tipi."}

    market_condition = gemini_market_state.get('verdict', 'trending')
    analysis = gemini_market_state.get('analysis', 'Gemini analizi alınamadı.')

    # Ekstra güvenlik kontrolü: Eğer market_condition bir string değilse, varsayılan değer ata
    if not isinstance(market_condition, str):
        market_condition = 'trending'
        analysis = "API'den geçersiz piyasa durumu verisi alındı. Varsayılan olarak trend durumu kabul edildi."
        print(analysis)

    initial_message = (
        f"**İlk Piyasa Analizi Hazır!**\n"
        f"Durum: **{market_condition.upper()}**\n"
        f"Analiz: {analysis}\n\n"
        f"Bot mevcut piyasa durumuna göre işlemlerine başlayacaktır."
    )
    await send_telegram_message(initial_message)
    
    bm = BinanceSocketManager(client)
    ts = bm.kline_socket(symbol=CFG['SYMBOL'], interval=CFG['INTERVAL'])

    async with ts as stream:
        while True:
            msg = await stream.recv()
            if msg.get('e') != 'kline':
                continue
            
            k = msg['k']
            close_price = float(k['c'])
            
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

                    # Piyasa durumu 'yatay' ise işlemi atla
                    if market_condition == 'sideways':
                        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Piyasa yatay. Sinyal reddedildi.")
                        message = (
                            f"**Sinyal Reddedildi!**\n\n"
                            f"Bot Adı: {CFG['BOT_NAME']}\n"
                            f"Sembol: {CFG['SYMBOL']}\n"
                            f"Oluşan Sinyal: **{signal}**\n\n"
                            f"**Gemini'ye göre piyasa şu anda yatay olduğu için işlem yapılmadı.**"
                        )
                        await send_telegram_message(message)
                        continue

                    # Piyasa durumu 'trend' ise işleme devam et
                    message = (
                        f"**{signal} Sinyali!**\n\n"
                        f"Bot Adı: {CFG['BOT_NAME']}\n"
                        f"Sembol: {CFG['SYMBOL']}\n"
                        f"Zaman Aralığı: {CFG['INTERVAL']}\n"
                        f"Fiyat: {close_price}\n"
                        f"MACD: {macd_line:.4f}\n"
                        f"Sinyal Hattı: {signal_line:.4f}\n"
                        f"Histogram: {histogram:.4f}"
                    )
                    await send_telegram_message(message)

    await client.close_connection()

if __name__ == "__main__":
    asyncio.run(run_bot())
