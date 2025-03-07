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

# Google Drive configuration
DRIVE_FOLDER_ID = os.getenv('DRIVE_FOLDER_ID')  # e.g., "1Aj6tq5f0emeDVfEfuRsfXaT-YjTAFA1i"
SERVICE_ACCOUNT_FILE = os.getenv('SERVICE_ACCOUNT_FILE')  # e.g., "./credentials.json"
SCOPES = ['https://www.googleapis.com/auth/drive']

# Initialize Google Drive service
creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
drive_service = build('drive', 'v3', credentials=creds)

# Initialize Tweepy (Twitter API)
client_v2 = tweepy.Client(
    consumer_key=CONSUMER_KEY,
    consumer_secret=CONSUMER_SECRET,
    access_token=ACCESS_KEY,
    access_token_secret=ACCESS_SECRET
)
auth = tweepy.OAuth1UserHandler(CONSUMER_KEY, CONSUMER_SECRET)
auth.set_access_token(ACCESS_KEY, ACCESS_SECRET)
api = tweepy.API(auth)

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
    return results.get('files', [])

def list_drive_files(folder_id):
    """Lists files in a given Google Drive folder."""
    query = f"'{folder_id}' in parents and trashed=false"
    results = drive_service.files().list(q=query, fields="files(id, name, mimeType)").execute()
    return results.get('files', [])

def download_file_from_drive(file_id, destination_path):
    """Downloads a file from Google Drive to a local destination."""
    request = drive_service.files().get_media(fileId=file_id)
    with io.FileIO(destination_path, 'wb') as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
    print(f"Downloaded {destination_path}")

def download_drive_folder(folder_id, local_folder):
    """
    Downloads all files in the specified Drive folder to a local directory.
    Returns the local folder path.
    """
    os.makedirs(local_folder, exist_ok=True)
    files = list_drive_files(folder_id)
    for f in files:
        file_name = f['name']
        destination = os.path.join(local_folder, file_name)
        # If file is a Google Doc, export as plain text
        if f['mimeType'] == 'application/vnd.google-apps.document':
            request = drive_service.files().export_media(fileId=f['id'], mimeType='text/plain')
            with io.FileIO(destination, 'wb') as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
        else:
            download_file_from_drive(f['id'], destination)
    return local_folder

# --- Tweeting Functions (Unchanged, except removal of tweeted_images tracking) ---

def get_alt_text_from_description(file_path):
    """Reads the first two lines from a description file to create alt text and returns full text."""
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            lines = file.readlines()
            alt_text = "".join(lines[:2]).strip()  # Use first two lines as alt text
            full_text = "".join(lines).strip()      # Full text for follow-up tweet
            return alt_text, full_text
    except Exception as e:
        print(f"Error reading description file {file_path}: {e}")
        return None, None

def tweet_images_from_folder(folder_path):
    """Tweets a random image from the specified local folder if valid images and a description file exist."""
    images = []
    description_file = None

    for item in os.listdir(folder_path):
        item_path = os.path.join(folder_path, item)
        if os.path.isfile(item_path):
            lower = item.lower()
            if lower.endswith(supported_formats):
                images.append(item_path)
            elif lower.startswith('descrip') and lower.endswith(supported_text_extensions):
                description_file = item_path

    if not images or not description_file:
        print(f"No images or description file found in folder: {folder_path}")
        return False

    # Shuffle images multiple times for randomness
    for _ in range(3):
        random.shuffle(images)
    selected_image = images[0]
    
    alt_text, full_text = get_alt_text_from_description(description_file)
    if not alt_text or not full_text:
        print("No valid alt text or full text found.")
        return False

    media_ids = []
    try:
        media = api.media_upload(selected_image)
        api.create_media_metadata(media.media_id, alt_text)
        media_ids.append(media.media_id)
    except tweepy.errors.TooManyRequests:
        print("Rate limit hit, sleeping for 4 hours...")
        sleep(6 * 60 * 60)
        return False
    except Exception as e:
        print(f"Error uploading image {selected_image}: {e}")
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
    downloads its contents to a temporary local folder, and tweets an image.
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
        download_drive_folder(folder['id'], local_folder)
        success = tweet_images_from_folder(local_folder)
        if success:
            break
        else:
            print("Retrying with another folder...")

def main():
    tweet_random_images()

if __name__ == "__main__":
    main()
