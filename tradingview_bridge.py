from flask import Flask, request, jsonify
import os
import telegram
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()

app = Flask(__name__)

# =========================================================================================
# TELEGRAM BOT AYARLARI
# =========================================================================================
# Telegram botunuzu başlatın
telegram_bot = telegram.Bot(token=os.getenv('TG_TOKEN'))
TELEGRAM_CHAT_ID = os.getenv('TG_CHAT_ID')

# =========================================================================================
# WEBHOOK ENDPOİNTİ
# Bu endpoint, my_bot.py dosyasından gelen POST sinyallerini alır.
# =========================================================================================
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    
    # Güvenlik kontrolü için SECRET anahtarını kontrol et
    if data.get('secret') != os.getenv('WEBHOOK_SECRET'):
        return jsonify({"code": "error", "message": "Geçersiz güvenlik anahtarı"}), 401

    symbol = data.get('symbol')
    signal_type = data.get('signal')

    if not symbol or not signal_type:
        return jsonify({"code": "error", "message": "Eksik parametreler"}), 400

    message = f"**TradingView Sinyali Alındı**\n\nSembol: {symbol}\nSinyal: {signal_type}"
    asyncio.run(send_telegram_message(message))

    return jsonify({"code": "ok", "message": "Sinyal başarıyla alındı ve işlendi"}), 200

# =========================================================================================
# DURUM VE SAĞLIK KONTROLÜ ENDPOİNTLERİ
# Bunlar, sunucunun çalışıp çalışmadığını kontrol etmek içindir.
# =========================================================================================
@app.route('/', methods=['GET'])
def home():
    return "TradingView Köprüsü çalışıyor! 🚀"

@app.route('/healthz', methods=['GET'])
def healthz():
    return "OK"

# =========================================================================================
# TELEGRAM MESAJI GÖNDERME FONKSİYONU
# =========================================================================================
async def send_telegram_message(text):
    if not telegram_bot or not TELEGRAM_CHAT_ID:
        print("Telegram ayarları eksik. Mesaj gönderilemedi.")
        return
    
    try:
        await telegram_bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=text,
            parse_mode='Markdown'
        )
    except Exception as e:
        print(f"Telegram'a mesaj gönderilirken hata oluştu: {e}")

# =========================================================================================
# UYGULAMAYI BAŞLATMA
# =========================================================================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
