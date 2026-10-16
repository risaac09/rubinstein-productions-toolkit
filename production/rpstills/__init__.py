"""rpstills: the offline front end of the stills lane. Index, cluster and
cull a shoot's camera JPEGs and build a review sheet, without touching RAW
files or Resolve. Runs on the ~/.venvs/rp-tools venv (Python 3.14) with Pillow, imagehash,
numpy and OpenCV from requirements.txt; the Vision helper needs swiftc."""
