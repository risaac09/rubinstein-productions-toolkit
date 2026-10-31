Fix a flaky CI test: the same-spec render test no longer compares the arnndn candidate's filter graph, which embeds loudness measured from an output that is not stable on the CI ffmpeg build. (#309)
