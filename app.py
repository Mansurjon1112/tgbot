from flask import Flask, request
import threading
import kanal  # sizning asosiy bot funksiyangiz shu faylda

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot ishlayapti!"

@app.route('/' + kanal.BOT_TOKEN, methods=['POST'])
def webhook():
    update = request.get_data().decode('utf-8')
    kanal.bot.process_new_updates([kanal.telebot.types.Update.de_json(update)])
    return 'OK', 200

def start_bot():
    threading.Thread(target=kanal.main).start()

if __name__ == "__main__":
    start_bot()
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 5000)))
