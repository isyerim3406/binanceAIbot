import asyncio
import os
import time
from datetime import datetime, timezone
from binance import AsyncClient, BinanceSocketManager
from dotenv import load_dotenv
import telegram
from telegram import constants
import requests
import json

# .env dosyasını yükle
load_dotenv()

# =========================================================================================
# GEMINI İLE İLETİŞİM
# =========================================================================================
async def generate_gemini_commentary(prompt):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("Gemini API anahtarı ayarlanmamış.")
        return "Gemini yorumu alınamadı."

    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-05-20:generateContent?key={api_key}"

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}]
    }

    try:
        response = requests.post(
            api_url,
            headers={'Content-Type': 'application/json'},
            data=json.dumps(payload),
            timeout=10 # 10 saniye zaman aşımı
        )
        response.raise_for_status()
        result = response.json()
        
        # Yanıttan yorumu çıkar
        candidate = result.get('candidates', [{}])[0]
        text_part = candidate.get('content', {}).get('parts', [{}])[0]
        commentary = text_part.get('text', "Yorum alınamadı.")
        return commentary
    except requests.exceptions.RequestException as e:
        print(f"Gemini API hatası: {e}")
        return "Piyasa yorumu alınamadı."

# =========================================================================================
# MACD STRATEJİSİ SINIFI
# =========================================================================================
class MACDStrategy:
    def __init__(self, options=None):
        options = options or {}
        self.fast_period = options.get('fast_period', 12)
        self.slow_period = options.get('slow_period', 26)
        self.signal_period = options.get('signal_period', 9)
        self.kline_history = []
        self.ema_fast = None
        self.ema_slow = None
        self.macd_line = []
        self.signal_line = []
        self.initial_capital = options.get('initial_capital', 100)
        self.qty_percent = options.get('qty_percent', 100)
        self.capital = self.initial_capital
        self.trades = []
        self.position_size = 0

    def calculate_ema(self, prices, period, prev_ema):
        if not prev_ema:
            return sum(prices) / len(prices)
        
        multiplier = 2 / (period + 1)
        return (prices[-1] - prev_ema) * multiplier + prev_ema

    def process_candle(self, timestamp, close_price):
        self.kline_history.append(close_price)
        if len(self.kline_history) > self.slow_period * 2:
            self.kline_history.pop(0)

        if len(self.kline_history) < self.slow_period:
            return {'signal': None}

        # EMA hesaplama
        self.ema_fast = self.calculate_ema(self.kline_history[-self.fast_period:], self.fast_period, self.ema_fast)
        self.ema_slow = self.calculate_ema(self.kline_history[-self.slow_period:], self.slow_period, self.ema_slow)
        
        if self.ema_fast and self.ema_slow:
            macd_val = self.ema_fast - self.ema_slow
            self.macd_line.append(macd_val)
            if len(self.macd_line) > self.signal_period * 2:
                self.macd_line.pop(0)

            # Sinyal hattı hesaplama
            if len(self.macd_line) >= self.signal_period:
                signal_val = self.calculate_ema(self.macd_line[-self.signal_period:], self.signal_period, None)
                self.signal_line.append(signal_val)
                
                if len(self.macd_line) >= 2 and len(self.signal_line) >= 2:
                    prev_macd = self.macd_line[-2]
                    prev_signal = self.signal_line[-2]
                    curr_macd = self.macd_line[-1]
                    curr_signal = self.signal_line[-1]

                    signal = None
                    if prev_macd < prev_signal and curr_macd > curr_signal:
                        signal = {'type': 'BUY', 'message': 'MACD AL Sinyali'}
                    elif prev_macd > prev_signal and curr_macd < curr_signal:
                        signal = {'type': 'SELL', 'message': 'MACD SAT Sinyali'}

                    return {'signal': signal}

        return {'signal': None}
    
    def get_avg_entry_price(self):
        entries = [t for t in self.trades if t['action'] == 'entry']
        return entries[-1]['price'] if entries else 0.0

    def open_position(self, side, price):
        qty = (self.capital * (self.qty_percent / 100)) / price
        self.position_size = qty if side == 'BUY' else -qty
        self.trades.append({'action': 'entry', 'type': side, 'price': price, 'quantity': qty})

    def close_position(self, price):
        if self.position_size == 0:
            return 0.0
        pnl = self.position_size * (price - self.get_avg_entry_price())
        self.capital += pnl
        self.trades.append({'action': 'exit', 'pnl': pnl, 'price': price})
        self.position_size = 0.0
        return pnl

# =========================================================================================
# BOT AYARLARI
# =========================================================================================
CFG = {
    'fast_period': int(os.getenv('FAST_PERIOD', 12)),
    'slow_period': int(os.getenv('SLOW_PERIOD', 26)),
    'signal_period': int(os.getenv('SIGNAL_PERIOD', 9)),
    'TRADE_SIZE_PERCENT': float(os.getenv('TRADE_SIZE_PERCENT', 100)),
    'SYMBOL': os.getenv('SYMBOL', 'ETHUSDT'),
    'INTERVAL': os.getenv('INTERVAL', '1h'),
    'INITIAL_CAPITAL': float(os.getenv('INITIAL_CAPITAL', 100)),
    'COOLDOWN_SECONDS': int(os.getenv('COOLDOWN_SECONDS', 3600)),
    'BOT_NAME': os.getenv('BOT_NAME', "MACD Botu Python"),
    'MODE': os.getenv('MODE', "Simülasyon"),
    'WEBHOOK_URL': os.getenv('WEBHOOK_URL', 'http://localhost:5000/webhook')
}

total_net_profit = 0.0
last_signal_time = 0.0

telegram_bot = None
if os.getenv('TG_TOKEN') and os.getenv('TG_CHAT_ID'):
    telegram_bot = telegram.Bot(token=os.getenv('TG_TOKEN'))

macd_strategy = MACDStrategy(options=CFG)

async def send_telegram_message(text):
    if not telegram_bot or not os.getenv('TG_CHAT_ID'):
        print("Telegram ayarlı değil veya gerekli ortam değişkenleri eksik.")
        return
    await telegram_bot.send_message(
        chat_id=os.getenv('TG_CHAT_ID'),
        text=text,
        parse_mode=constants.ParseMode.MARKDOWN
    )

def send_webhook_signal(symbol, signal_type):
    payload = {
        'secret': os.getenv('WEBHOOK_SECRET', 'YOUR_STRONG_SECRET_KEY'),
        'symbol': symbol,
        'signal': signal_type
    }
    
    # Binance API anahtarları tanımlanmışsa, gerçek işlem modunda çalışır
    if os.getenv('BINANCE_API_KEY') and os.getenv('BINANCE_SECRET'):
        url = CFG['WEBHOOK_URL']
        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
            print(f"Webhook sinyali gönderildi. Yanıt: {response.json()}")
        except requests.exceptions.RequestException as e:
            print(f"Webhook sinyali gönderilirken hata oluştu: {e}")
    else:
        print("Binance API anahtarları tanımlı değil. Simülasyon modunda çalışıyor.")

# =========================================================================================
# BOT ANA DÖNGÜSÜ
# =========================================================================================
async def run_bot():
    global total_net_profit, last_signal_time
    print(f"🤖 {CFG['BOT_NAME']} başlatılıyor...")

    client = await AsyncClient.create()
    bm = BinanceSocketManager(client)

    # Başlangıçta geçmiş 500 mum
    candles = await client.get_klines(symbol=CFG['SYMBOL'], interval=CFG['INTERVAL'], limit=500)
    for c in candles:
        macd_strategy.process_candle(c[0], float(c[4]))

    # ✅ Bot başlatıldı mesajı
    msg = (
        f"**{CFG['BOT_NAME']} Başladı!**\n"
        f"Mod: {CFG['MODE']}\n"
        f"Sembol: {CFG['SYMBOL']}\n"
        f"Zaman Aralığı: {CFG['INTERVAL']}\n"
    )
    await send_telegram_message(msg)

    ts = bm.kline_socket(symbol=CFG['SYMBOL'], interval=CFG['INTERVAL'])
    async with ts as stream:
        while True:
            kmsg = await stream.recv()
            if kmsg.get('e') != 'kline':
                continue
            k = kmsg['k']
            if k['x']: # Mum kapandığında
                ts = k['t']
                close_price = float(k['c'])

                # 📊 Bar kapanışını logla
                ts_str = datetime.fromtimestamp(ts / 1000, timezone.utc).strftime('%d.%m.%Y %H:%M:%S')
                print(f"📊 Yeni bar alındı | Sembol: {CFG['SYMBOL']} | Zaman Aralığı: {CFG['INTERVAL']} | Kapanış: {close_price:.2f} | {ts_str}")

                result = macd_strategy.process_candle(ts, close_price)
                if result['signal']:
                    now = time.time()
                    if last_signal_time and (now - last_signal_time) < CFG['COOLDOWN_SECONDS']:
                        continue

                    side = result['signal']['type']
                    pnl = macd_strategy.close_position(close_price)
                    total_net_profit += pnl
                    macd_strategy.open_position(side, close_price)
                    last_signal_time = now
                    
                    # Webhook sinyali gönder
                    send_webhook_signal(CFG['SYMBOL'], side)

                    ts_str = datetime.fromtimestamp(ts/1000, timezone.utc).strftime("%d.%m.%Y - %H:%M")
                    percent_pnl = (pnl / macd_strategy.initial_capital) * 100 if macd_strategy.initial_capital else 0
                    total_percent = (total_net_profit / macd_strategy.initial_capital) * 100 if macd_strategy.initial_capital else 0
                    
                    # Gemini API'den piyasa yorumu al
                    prompt = (f"{CFG['SYMBOL']} {CFG['INTERVAL']} zaman aralığında {side} sinyali verdi. "
                              f"Bu sinyalin oluştuğu anki piyasa hakkında kısa, uzman bir yorum yap.")
                    gemini_commentary = await generate_gemini_commentary(prompt)

                    msg = (
                        f"**{side} Emri Gerçekleşti!**\n\n"
                        f"Bot Adı: {CFG['BOT_NAME']}\n"
                        f"Mod: {CFG['MODE']}\n"
                        f"Sembol: {CFG['SYMBOL']}\n"
                        f"Zaman Aralığı: {CFG['INTERVAL']}\n"
                        f"Sinyal:{result['signal']['message']}\n"
                        f"Fiyat:{close_price}\n"
                        f"Zaman : {ts_str}\n"
                        f"Bu İşlemden Kar/Zarar : % {percent_pnl:.2f} ({pnl:.2f} USDT)\n"
                        f"Toplam Net Kar/Zarar : % {total_percent:.2f} ({total_net_profit:.2f} USDT)\n\n"
                        f"**Piyasa Yorumu:**\n{gemini_commentary}"
                    )
                    await send_telegram_message(msg)

    await client.close_connection()

# =========================================================================================
# UYGULAMAYI ÇALIŞTIR
# =========================================================================================
if __name__ == '__main__':
    asyncio.run(run_bot())
