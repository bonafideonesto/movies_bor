import subprocess
import sys
import os

def main():
    # Запускаем Flask сервер
    print("🚀 Запуск Flask сервера...")
    flask_process = subprocess.Popen(
        [sys.executable, "-m", "flask", "run", 
         "--host=0.0.0.0", 
         "--port=10000"],
        env=os.environ
    )
    
    # Даем Flask время на запуск
    import time
    time.sleep(2)
    
    # Запускаем бота
    print("🤖 Запуск Telegram бота...")
    bot_process = subprocess.Popen(
        [sys.executable, "bot.py"],
        env=os.environ
    )
    
    try:
        # Ждем завершения процессов
        flask_process.wait()
        bot_process.wait()
    except KeyboardInterrupt:
        print("\n🛑 Остановка приложения...")
        flask_process.terminate()
        bot_process.terminate()

if __name__ == "__main__":
    main()
