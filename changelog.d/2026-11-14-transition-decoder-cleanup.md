The draft farm's transition post-pass kills its decoder when the encoder quits, so a failed write leaves no ffmpeg running and no stray stderr, and a test pins it.
