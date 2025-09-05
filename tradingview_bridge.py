import os
from flask import Flask, request, jsonify
from binance.client import Client

# Flask uygulamasını başlat
app = Flask(__name__)

# =========================================================================================
# API VE AYARLAR
# =========================================================================================
# Binance API anahtarları (Gerçek işlem yapmak için gereklidir)
BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY')
BINANCE_SECRET = os.environ.get('BINANCE_SECRET')

# Sinyalleri doğrulamak için gizli anahtar
SECRET_KEY = "YOUR_STRONG_SECRET_KEY"

# =========================================================================================
# BİNANCE İŞLEM FONKSİYONU
# =========================================================================================
def execute_trade(symbol: str, signal: str, quantity: float):
    """
    Belirtilen sembol ve sinyale göre Binance'da işlem emri verir.
    """
    if not BINANCE_API_KEY or not BINANCE_SECRET:
        return {"status": "error", "message": "Binance API anahtarları eksik. İşlem yapılamadı."}

    client = Client(BINANCE_API_KEY, BINANCE_SECRET)

    # Alım veya satım emri
    if signal == 'BUY':
        try:
            order = client.create_order(
                symbol=symbol,
                side='BUY',
                type='MARKET',
                quantity=quantity
            )
            return {"status": "success", "message": "Alım emri başarıyla verildi.", "order": order}
        except Exception as e:
            return {"status": "error", "message": f"Alım emri sırasında hata oluştu: {e}"}
    elif signal == 'SELL':
        try:
            order = client.create_order(
                symbol=symbol,
                side='SELL',
                type='MARKET',
                quantity=quantity
            )
            return {"status": "success", "message": "Satım emri başarıyla verildi.", "order": order}
        except Exception as e:
            return {"status": "error", "message": f"Satım emri sırasında hata oluştu: {e}"}
    else:
        return {"status": "error", "message": "Geçersiz sinyal. (BUY veya SELL olmalı)"}

# =========================================================================================
# WEBHOOK ENDPOİNT'İ
# =========================================================================================
@app.route('/webhook', methods=['POST'])
def webhook():
    """
    My_bot.py'den gelen sinyal isteğini işler.
    """
    data = request.json
    
    # Gizli anahtarı kontrol et
    if data.get('secret') != SECRET_KEY:
        return jsonify({"status": "error", "message": "Gizli anahtar eşleşmiyor. İzinsiz erişim."}), 403

    symbol = data.get('symbol')
    signal = data.get('signal')
    quantity = data.get('quantity')

    if not all([symbol, signal, quantity]):
        return jsonify({"status": "error", "message": "Eksik parametreler. (symbol, signal, quantity)"}), 400

    print(f"Sinyal alındı: {symbol} için {signal} sinyali geldi.")
    
    # Gerçek işlemi gerçekleştir
    result = execute_trade(symbol, signal, quantity)
    
    return jsonify(result), 200

# =========================================================================================
# UYGULAMA BAŞLANGICI
# =========================================================================================
if __name__ == '__main__':
    # Flask uygulamasını yerel sunucuda çalıştır
    app.run(host='0.0.0.0', port=5000)
