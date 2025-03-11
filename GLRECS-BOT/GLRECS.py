import os
import random
import tweepy
from time import sleep
from datetime import datetime
import pytz
from dotenv import load_dotenv
import io

# Google API imports
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2.service_account import Credentials

# Load environment variables
load_dotenv()
CONSUMER_KEY = os.getenv('CONSUMER_KEY')
CONSUMER_SECRET = os.getenv('CONSUMER_SECRET')
ACCESS_KEY = os.getenv('ACCESS_KEY')
ACCESS_SECRET = os.getenv('ACCESS_SECRET')

# Debug: Print environment variables
print("Loaded environment variables:")
print(f"CONSUMER_KEY: {CONSUMER_KEY}")
print(f"CONSUMER_SECRET: {CONSUMER_SECRET}")
print(f"ACCESS_KEY: {ACCESS_KEY}")
print(f"ACCESS_SECRET: {ACCESS_SECRET}")

# Google Drive configuration
DRIVE_FOLDER_ID = os.getenv('DRIVE_FOLDER_ID')  # e.g., "1Aj6tq5f0emeDVfEfuRsfXaT-YjTAFA1i"
SERVICE_ACCOUNT_FILE = os.getenv('SERVICE_ACCOUNT_FILE')  # e.g., "./credentials.json"
SCOPES = ['https://www.googleapis.com/auth/drive']

# Debug: Print Google Drive configuration
print(f"DRIVE_FOLDER_ID: {DRIVE_FOLDER_ID}")
print(f"SERVICE_ACCOUNT_FILE: {SERVICE_ACCOUNT_FILE}")

# Initialize Google Drive service
try:
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    drive_service = build('drive', 'v3', credentials=creds)
    print("Google Drive service initialized successfully.")
except Exception as e:
    print(f"Error initializing Google Drive service: {e}")
    exit(1)

# Initialize Tweepy (Twitter API)
try:
    client_v2 = tweepy.Client(
        consumer_key=CONSUMER_KEY,
        consumer_secret=CONSUMER_SECRET,
        access_token=ACCESS_KEY,
        access_token_secret=ACCESS_SECRET
    )
    auth = tweepy.OAuth1UserHandler(CONSUMER_KEY, CONSUMER_SECRET)
    auth.set_access_token(ACCESS_KEY, ACCESS_SECRET)
    api = tweepy.API(auth)
    print("Twitter API initialized successfully.")
except Exception as e:
    print(f"Error initializing Twitter API: {e}")
    exit(1)

# --- Configuration ---
# Local temporary directory to download the Drive folder contents
local_base_folder = './GLRECS_temp'
# Supported image formats
supported_formats = ('.jpg', '.jpeg', '.png', '.gif')
# Supported description file extensions (added .doc and .docx)
supported_text_extensions = ('.txt', '.rtf', '.doc', '.docx')

# Miami timezone
miami_tz = pytz.timezone('America/New_York')

# --- Google Drive Helper Functions ---

def list_drive_folders(parent_id):
    """Lists subfolders in the given Google Drive folder."""
    query = f"'{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    results = drive_service.files().list(q=query, fields="files(id, name)").execute()
    print(f"Found {len(results.get('files', []))} folders in Drive.")
    return results.get('files', [])

def list_drive_files(folder_id):
    """Lists files in a given Google Drive folder."""
    query = f"'{folder_id}' in parents and trashed=false"
    results = drive_service.files().list(q=query, fields="files(id, name, mimeType)").execute()
    print(f"Found {len(results.get('files', []))} files in folder {folder_id}.")
    return results.get('files', [])

def download_file_from_drive(file_id, destination_path):
    """Downloads a file from Google Drive to a local destination."""
    try:
        request = drive_service.files().get_media(fileId=file_id)
        with io.FileIO(destination_path, 'wb') as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
        print(f"Downloaded {destination_path}")
    except Exception as e:
        print(f"Error downloading file {file_id}: {e}")

# --- Tweeting Functions ---

def get_alt_text_from_description(file_path):
    """Reads the first two lines from a description file to create alt text and returns full text."""
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            lines = file.readlines()
            alt_text = "".join(lines[:2]).strip()  # Use first two lines as alt text
            full_text = "".join(lines).strip()      # Full text for follow-up tweet
            print(f"Read alt text: {alt_text}")
            return alt_text, full_text
    except Exception as e:
        print(f"Error reading description file {file_path}: {e}")
        return None, None

def select_and_download_files(files, local_folder):
    """Randomly selects one image and one description file, and downloads them."""
    images = []
    description_file = None

    # Randomly shuffle files to avoid always selecting the same files
    random.shuffle(files)

    for file in files:
        file_name = file['name']
        file_id = file['id']
        lower = file_name.lower()

        # Select image files
        if lower.endswith(supported_formats):
            images.append({'id': file_id, 'name': file_name})

        # Select description files
        elif lower.startswith('descrip') and lower.endswith(supported_text_extensions):
            description_file = {'id': file_id, 'name': file_name}

    # Check if both image and description file exist
    if images and description_file:
        selected_image = random.choice(images)  # Pick a random image from the list
        print(f"Selected image: {selected_image['name']}")

        # Download selected image and description file
        download_file_from_drive(selected_image['id'], os.path.join(local_folder, selected_image['name']))
        download_file_from_drive(description_file['id'], os.path.join(local_folder, description_file['name']))

        return selected_image, description_file
    else:
        print("No valid image or description file found.")
        return None, None

def tweet_images_from_folder(folder_path):
    """Tweets a random image from the specified local folder if valid images and a description file exist."""
    files = list_drive_files(DRIVE_FOLDER_ID)
    selected_image, description_file = select_and_download_files(files, folder_path)

    if not selected_image or not description_file:
        return False

    alt_text, full_text = get_alt_text_from_description(os.path.join(folder_path, description_file['name']))
    if not alt_text or not full_text:
        print("No valid alt text or full text found.")
        return False

    media_ids = []
    try:
        media = api.media_upload(os.path.join(folder_path, selected_image['name']))
        api.create_media_metadata(media.media_id, alt_text)
        media_ids.append(media.media_id)
        print(f"Uploaded media with ID: {media.media_id}")
    except tweepy.errors.TooManyRequests:
        print("Rate limit hit, sleeping for 4 hours...")
        sleep(6 * 60 * 60)
        return False
    except Exception as e:
        print(f"Error uploading image {selected_image['name']}: {e}")
        return False

    if media_ids:
        try:
            tweet_text = "₊ ⊹ ❤︎ sapphic recommendations ❤︎ ⊹ ₊"
            response = client_v2.create_tweet(text=tweet_text, media_ids=media_ids)
            client_v2.create_tweet(text=full_text, in_reply_to_tweet_id=response.data['id'])
            current_time = datetime.now(miami_tz).strftime('%Y-%m-%d %I:%M %p')
            print(f"Rec Tweeted: {alt_text} at {current_time}")
        except Exception as e:
            print(f"Error tweeting text: {e}")
            return False

    return True

def tweet_random_images():
    """
    Randomly selects a series folder from the Google Drive folder (GLRECS),
    downloads only the necessary files (image and description), and tweets an image.
    """
    if not DRIVE_FOLDER_ID:
        print("No DRIVE_FOLDER_ID provided.")
        return

    drive_folders = list_drive_folders(DRIVE_FOLDER_ID)
    if not drive_folders:
        print("No eligible folders found on Google Drive.")
        return

    # Shuffle folders multiple times for extra randomness
    for _ in range(3):
        random.shuffle(drive_folders)

    for folder in drive_folders:
        print(f"Selected Drive folder: {folder['name']} (ID: {folder['id']})")
        local_folder = os.path.join(local_base_folder, folder['name'])
        success = tweet_images_from_folder(local_folder)
        if success:
            break
        else:
            print("Retrying with another folder...")

def main():
    tweet_random_images()

if __name__ == "__main__":
    main()
