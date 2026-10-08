"""rpstills: the offline front end of the stills lane. Index, cluster and
cull a shoot's camera JPEGs and build a review sheet, without touching RAW
files or Resolve. Runs on macOS's /usr/bin/python3 with Pillow, imagehash,
numpy and OpenCV, which live there; the Vision helper needs swiftc."""
