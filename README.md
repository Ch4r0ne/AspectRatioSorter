# AspectRatioSorter

This script sorts image and video files in the landscape and portrait subfolders in the original folder.

### How to use
1. Download the script (AspectRatioSorter.py)
2. Run the script
3. Enter the folder path where the files to be sorted are located
4. The program will go through all the files in the folder and move them to the "landscape" or "portrait" subfolder

### Requirements
- Python 3.x
- opencv-python
- tqdm (optional, but recommended for a better user experience)

You can install these libraries by running the following command in your command prompt:

    pip install opencv-python tqdm

### Supported file types
 JPEG, JPG, PNG, MOV, MP4

### Note
- The script will not sort files in subfolders within the target folder.
- The script will not overwrite files in the subfolders.
- You can use the command `pyinstaller AspectRatioSorter.py --hidden-import tqdm --hidden-import opencv-python` to repack the script AspectRatioSorter.py into a standalone executable using the PyInstaller library

### How the script works
The script starts by creating the subfolders where the files will be sorted. Then it lists all the files in the directory and exclude directories. Then it creates a progress bar using tqdm and iterates through all the files in the directory. Then it reads the image file or open the video file and gets the width and height of the image or video. Then it calculates the aspect ratio of the image or video and determine if the image/video is portrait or landscape. Then it creates the subfolder if it doesn't exist and move the file to the appropriate subfolder. Finally, it updates the progress bar
