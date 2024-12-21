getenv=True

# Huggingface caching: https://huggingface.co/docs/huggingface_hub/en/guides/manage-cache
export HF_HOME="/home/john/hf/.cache"

# CUDA
export CUDA_HOME="/usr/local/cuda"
export PYTORCH_KERNEL_CACHE_PATH="/home/john/hf/.cache/torch"
# export PYOPENGL_PLATFORM="egl"
export PYOPENGL_PLATFORM="egl"
export LD_LIBRARY_PATH=$CUDA_HOME/lib64:$LD_LIBRARY_PATH

# OPENAI API Key: https://help.openai.com/en/articles/4936850-where-do-i-find-my-openai-api-key
export OPENAI_API_KEY=""

# CONDA
export PYTHONPATH=$PYTHONPATH:$(pwd)
source /home/john/miniconda3/bin/activate PuzzleAvatar
