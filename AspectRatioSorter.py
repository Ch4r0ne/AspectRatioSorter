import os
import cv2
from tqdm import tqdm
import concurrent.futures

def process_file(filename, directory):
    filename = filename.lower()
    filepath = os.path.join(directory, filename)
    cap = None
    if filename.endswith(".mp4") or filename.endswith(".mov"):
        cap = cv2.VideoCapture(filepath)
        fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(3))
        height = int(cap.get(4))
    elif filename.endswith('.jpg') or filename.endswith('.jpeg') or filename.endswith('.png'):
        im = cv2.imread(filepath)
        height, width, channel = im.shape
    else:
        return
    aspect_ratio = width / height
    if aspect_ratio < 1:
        subfolder = "portrait"
    else:
        subfolder = "landscape"
    subfolder_path = os.path.join(directory, subfolder)
    if not os.path.exists(subfolder_path):
        os.mkdir(subfolder_path)
    new_filepath = os.path.join(subfolder_path, filename)
    if cap is not None:
        cap.release()
    os.rename(filepath, new_filepath)

print("The skript sorted image and video files in the landscape and portrait subfolders in the original folder.")
print("")

print(r"Example path: C:\Users\Username\Documents\MediaFolder")
directory = input("Enter the path of the folder:")

os.makedirs(directory + '/landscape', exist_ok=True)
os.makedirs(directory + '/portrait', exist_ok=True)

files = [file for file in os.listdir(directory) if not os.path.isdir(os.path.join(directory, file))]

with tqdm(total=len(files)) as pbar:
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = {executor.submit(process_file, file, directory): file for file in files}
        for future in concurrent.futures.as_completed(futures):
            pbar.update(1)

input("Press the Enter key to close the window")
