import torch
import torch.nn as nn
from torch.nn import init
import functools
from torch.autograd import Variable
import numpy as np

class BasicBlock(nn.Module):
    def __init__(self, channel_num):
        super(BasicBlock, self).__init__()
        self.conv_block1 = nn.Sequential(
            nn.Conv2d(channel_num, channel_num, 3, padding=1),
            nn.BatchNorm2d(channel_num),
            nn.ReLU()
        )
        self.conv_block2 = nn.Sequential(
            nn.Conv2d(channel_num, channel_num, 3, padding=1),
            nn.BatchNorm2d(channel_num),
            nn.ReLU()
        )

    def forward(self, x):

        residual = x
        out = self.conv_block1(x)
        out = self.conv_block2(out)
        try:
            out = out + residual
        except:
            print("Issue")
        print(f"Output shape prior to RELU: {out.shape}")
        out = nn.ReLU()(out)
        print(f"Type of outward tensor: {type(out)}")
        print(out.shape)
        return out