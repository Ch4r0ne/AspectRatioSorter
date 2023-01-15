import os
import cv2
from tqdm import tqdm

# Inform the user
print("The skript sorted image and video files in the landscape and portrait subfolders in the original folder.")
print("")

# Ask the user for the path of the folder containing the files
print(r"Example path: C:\Users\Username\Documents\MediaFolder")
directory = input("Enter the path of the folder:")

# create the subfolders where the files will be sorted
os.makedirs(directory + '/landscape', exist_ok=True)
os.makedirs(directory + '/portrait', exist_ok=True)

# list all the files in the directory and exclude directories
files = [file for file in os.listdir(directory) if not os.path.isdir(os.path.join(directory, file))]

# create a progress bar using tqdm
with tqdm(total=len(files)) as pbar:
    # iterate through all the files in the directory
    for filename in files:
        filename = filename.lower()
        filepath = os.path.join(directory, filename)
        cap = None
        if filename.endswith(".mp4") or filename.endswith(".mov"):
            # open the video file
            cap = cv2.VideoCapture(filepath)
            # get the frames per second (fps) of the video
            fps = cap.get(cv2.CAP_PROP_FPS)
            # get the frame dimensions
            width = int(cap.get(3))
            height = int(cap.get(4))
        elif filename.endswith('.jpg') or filename.endswith('.jpeg') or filename.endswith('.png'):
            # read the image file
            im = cv2.imread(filepath)
            # get the width and height of the image
            height, width, channel = im.shape
        else:
            continue
        # calculate the aspect ratio
        aspect_ratio = width / height
        # determine if the image/video is portrait or landscape
        if aspect_ratio < 1:
            subfolder = "portrait"
        else:
            subfolder = "landscape"
        # create the subfolder if it doesn't exist
        subfolder_path = os.path.join(directory, subfolder)
        if not os.path.exists(subfolder_path):
            os.mkdir(subfolder_path)
        # move the file to the appropriate subfolder
        new_filepath = os.path.join(subfolder_path, filename)
        if cap is not None:
            cap.release()
        os.rename(filepath, new_filepath)
        # update the progress bar
        pbar.update(1)

input("Press the Enter key to close the window")