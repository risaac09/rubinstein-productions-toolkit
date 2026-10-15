"""lutlab: decompose a 3D LUT or an image pair into a few readable look parameters, and rebuild it."""

import warnings

# numpy 2.0 on Apple's Accelerate raises spurious divide/overflow flags inside
# matmul on finite inputs; the results are correct. Silence only those.
warnings.filterwarnings("ignore", message=r".*encountered in matmul", category=RuntimeWarning)
