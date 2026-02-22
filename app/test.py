import threading
import time
from sqlalchemy.orm import Session
from database.database import engine  # Import your engine
from scripts.scraper_calls import quali_scraper

def debug_scraper():
    # 1. Setup mock state
    test_messages = []
    test_lock = threading.Lock()
    
    # 2. Setup Database Session
    # This creates a real connection to your scraper_data.db
    db_session = Session(bind=engine)
    
    # Set the user ID you want to test with (as requested)
    TEST_USER_ID = 1

    print(f"--- Starting Scraper Debug Session (User ID: {TEST_USER_ID}) ---")

    # 3. Message monitor (unchanged logic)
    def message_monitor():
        last_idx = 0
        while True:
            with test_lock:
                if last_idx < len(test_messages):
                    for i in range(last_idx, len(test_messages)):
                        print(f"[SCRAPER LOG]: {test_messages[i]}")
                    last_idx = len(test_messages)
                
                # Exit conditions
                if any("done" in str(m).lower() or "error" in str(m).lower() for m in test_messages):
                    break
            time.sleep(0.5)

    monitor_thread = threading.Thread(target=message_monitor, daemon=True)
    monitor_thread.start()

    # 4. Execute the scraper with the 4 required arguments
    # try:
        # Arguments: (logs, lock, user_id, db_session)
    quali_scraper(test_messages, test_lock, TEST_USER_ID, db_session)
        
    # except Exception as e:
    #     print(f"\n[CRITICAL ERROR]: {str(e)}")
    # finally:
    db_session.close()  # Always close the session
    print("\n--- Scraper Finished ---")
    time.sleep(1)
    
    print("\nFull Message History Log:")
    for msg in test_messages:
        print(msg)

if __name__ == "__main__":
    debug_scraper()