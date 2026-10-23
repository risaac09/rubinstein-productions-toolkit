Give ograf-render's headless Chrome up to 30 s to open its DevTools port and keep it out of /dev/shm, so a cold start on a busy CI runner does not fail the render.
