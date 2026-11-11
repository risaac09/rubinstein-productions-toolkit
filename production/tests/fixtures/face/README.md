# Face fixture

`portrait.jpg` (512 x 600) is a head-and-shoulders portrait photograph that macOS Vision
finds a face in (one box, confidence 0.81, centred at 0.525 of the width). It is the face
for `TestOnARealFace` in `production/tests/test_reframe.py`, which sets it on a grey
1920x1080 clip and runs the face planner through the real Vision helper. Those tests skip
off macOS.

Source: the sample image bundled with matplotlib 3.9.4 (`mpl-data/sample_data`), copied
unchanged. It is a US Navy photograph, and a work of the US federal government is in the
public domain. Licence stated from matplotlib's own sample-data provenance and the
photograph's public-domain status; not re-checked against the original archive when it was
added. No client or shoot media goes in this folder.
