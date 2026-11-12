# Face fixture

`portrait.jpg` (512 x 600) is a head-and-shoulders portrait photograph that macOS Vision
finds a face in (one box, confidence 0.81, centred at 0.525 of the width). It is the face
for `TestOnARealFace` in `production/tests/test_reframe.py`, which sets it on a grey
1920x1080 clip and runs the face planner through the real Vision helper. Those tests skip
off macOS.

Source: the sample image bundled with matplotlib 3.9.4 (`mpl-data/sample_data/grace_hopper.jpg`,
sha256 a8ca6d73...7130), copied unchanged. It is the portrait of Rear Admiral Grace Hopper that
Wikimedia Commons files as "Commodore Grace M. Hopper, USN (covered).jpg", credited to James S.
Davis, a US Navy photographer, with the licence "Public domain" (a work of a US federal government
employee). Checked on 2026-11-12 against the Commons file page's metadata through the Commons API.
That the matplotlib file is a crop of that photograph is the usual attribution and was not
matched pixel for pixel; the fixture carries no copyright claim of this repository's own. If the
chain is ever doubted, replace the file with a portrait generated or photographed for the project.
No client or shoot media goes in this folder.
