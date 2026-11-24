CI installs ffmpeg through scripts/ci-install-ffmpeg, which limits each apt call and tries a hung or failed one again, up to three times.
