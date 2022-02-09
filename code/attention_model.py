from sys import path_hooks
import torch
import torch.nn as nn
import torch.nn.functional as F
from attention import ProjectorBlock, SpatialAttn, TemporalAttn
import math
from torchvision import models
import torch.nn.functional as F
"""
VGG-16 with attention
"""
class Resnext50(nn.Module):
    def __init__(self, n_classes):
        super().__init__()
        resnet = models.resnext50_32x4d(pretrained=False)
        resnet.fc = nn.Sequential(
            nn.Dropout(p=0.2),
            nn.Linear(in_features=resnet.fc.in_features, out_features=n_classes)
        )
        self.base_model = resnet
        self.sigm = nn.Sigmoid()

    def forward(self, x):
        return self.sigm(self.base_model(x))



    def _make_layer(self, input_channels, out_features, kernel_size):
        return nn.Sequential(*[
            nn.Conv2d(input_channels, out_features, kernel_size),
            nn.BatchNorm2d(out_features, affine=True),
            nn.MaxPool2d(kernel_size=2, stride=2),
            nn.ReLU(inplace=True)
        ])

    def _initialize_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
                if m.bias is not None:
                    nn.init.constant_(m.bias, 0)
            elif isinstance(m, nn.BatchNorm2d):
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)
            elif isinstance(m, nn.Linear):
                nn.init.normal_(m.weight, 0, 0.01)
                nn.init.constant_(m.bias, 0)


class BaseModel(nn.Module):
    def __init__(self, n_classes) -> None:
        super().__init__()
        self.conv_1 = self._make_layer(3, 64, 3)
        self.conv_2 = self._make_layer(64, 128, 3)
        self.conv_3 = self._make_layer(128, 256, 3)
        self.n_classes = n_classes

    def _make_layer(self, input_channels, out_features, kernel_size):
        return nn.Sequential(*[
            nn.Conv2d(input_channels, out_features, kernel_size),
            nn.BatchNorm2d(out_features, affine=True),
            nn.MaxPool2d(kernel_size=2, stride=2),
            nn.ReLU(inplace=True)
        ])

    def forward(self, x):
        output_1 = self.conv_1(x)
        output_2 = self.conv_2(output_1)
        output_3 = self.conv_3(output_2)
        pooling_layer = F.avg_pool2d(output_3, output_3.shape[-1])
        flat_layer = pooling_layer.view(pooling_layer.shape[0], -1)
        final_activation_function = nn.Linear(
            in_features=flat_layer.shape[1],
            out_features=self.n_classes,
            bias=True
            )
        return final_activation_function(flat_layer)
