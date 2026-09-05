"""
Конфигурация для принудительного использования CPU в Demucs
"""
import os
import torch

# Устанавливаем CPU как устройство по умолчанию
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["TORCH_DEVICE"] = "cpu"
torch.device('cpu')
