A `reframe-pan` fade now writes its own key on frame 0 at full brightness. Resolve adds that key by itself, so the read-back failed on a fade that had in fact been built and rendered correctly.
