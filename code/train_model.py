from attention_model_v2 import AttModelV2
from attention_model_v1 import AttModelV1
from resnet_models import get_resnet_18
from dataset import NusDataset
import os
import numpy as np
import matplotlib.pyplot as plt
import numpy as np
import random
import torch
from torch.utils.data import DataLoader
import torch.nn as nn
from sklearn.metrics import precision_score, recall_score, f1_score, accuracy_score, hamming_loss, average_precision_score
from datetime import datetime
import pandas as pd
from tqdm import tqdm
import torch.distributed as dist
from torch.nn.parallel import DistributedDataParallel as DDP
from torch.utils.data.distributed import DistributedSampler
from torch.distributed.optim import ZeroRedundancyOptimizer
from torch import optim
import torch.distributed.autograd as dist_autograd
from mean_average_precision import MetricBuilder
from torch.cuda.amp import GradScaler, autocast
import argparse
from coco_dataset import DataSet
import ml_metrics
import pdb
import numpy as np

def average_precision(output, target):
    epsilon = 1e-8

    # sort examples
    indices = output.argsort()[::-1]
    # Computes prec@i
    total_count_ = np.cumsum(np.ones((len(output), 1)))

    target_ = target[indices]
    ind = target_ == 1
    pos_count_ = np.cumsum(ind)
    total = pos_count_[-1]
    pos_count_[np.logical_not(ind)] = 0
    pp = pos_count_ / total_count_
    precision_at_i_ = np.sum(pp)
    precision_at_i = precision_at_i_ / (total + epsilon)

    return precision_at_i


def mAP(targs, preds):
    """Returns the model's average precision for each class
    Return:
        ap (FloatTensor): 1xK tensor, with avg precision for each class k
    """

    if np.size(preds) == 0:
        return 0
    ap = np.zeros((preds.shape[1]))
    # compute average precision for each class
    for k in range(preds.shape[1]):
        # sort scores
        scores = preds[:, k]
        targets = targs[:, k]
        # compute average precision
        ap[k] = average_precision(scores, targets)
    return 100 * ap.mean()



def collate_fn(batch):
    batch = list(filter(lambda x: x is not None, batch))
    return torch.utils.data.dataloader.default_collate(batch)

# Use threshold to define predicted labels and invoke sklearn's metrics with different averaging strategies.
def calculate_metrics(pred, target, num_classes, threshold=0.5):
    pred_bool = np.array(pred > threshold, dtype=float)
    with np.errstate(divide='ignore', invalid='ignore'):
        WAP = average_precision_score(target, pred, average='weighted')
        WAP = 0 if np.isnan(WAP) else WAP
        MAP = average_precision_score(target, pred, average='macro')
        MAP = 0 if np.isnan(MAP) else MAP
        mean_AP = mAP(target, pred)
        mean_AP = 0 if np.isnan(mean_AP) else mean_AP
    return {
        'accuracy': accuracy_score(y_true=target, y_pred=pred_bool),
        'WAP': WAP,
        'MAP': MAP,
        "hamming_loss": hamming_loss(y_true=target, y_pred=pred_bool),
        "mAP": mean_AP
    }

def load_data_coco():
    train_coco, test_coco = (
        DataSet(
            ["coco_data/coco/train_coco2014.json"],
            [],
            500,
            "coco"
        ),
        DataSet(
            ["coco_data/coco/val_coco2014.json"],
            [],
            500,
            "coco"
        )
    )

    dataset_train, dataset_val = parralelize_dataset(train_coco, test_coco)
    return dataset_train, dataset_val


def parralelize_dataset(train, val):

    sampler_train = DistributedSampler(train)
    sampler_val = DistributedSampler(val)

    train_dataloader = DataLoader(
        train,
        batch_size=BATCH_SIZE,
        sampler=sampler_train,
        num_workers=8,
        pin_memory=True,
        collate_fn=collate_fn)

    test_dataloader = DataLoader(
        val,
        batch_size=BATCH_SIZE,
        sampler=sampler_val,
        num_workers=8,
        pin_memory=True,
        collate_fn=collate_fn)

    return train_dataloader, test_dataloader

def load_data_nus(train_path, test_path):

    dataset_train = NusDataset(
        IMAGE_PATH,
        os.path.join(META_PATH, train_path),
        None)

    dataset_val = NusDataset(
        IMAGE_PATH,
        os.path.join(META_PATH, test_path),
        None)

    train_dataloader, test_dataloader = parralelize_dataset(dataset_train, dataset_val)

    return train_dataloader, test_dataloader

LEARNING_RATE = WEIGHT_DECAY = 1e-4
MAX_EPOCH_NUMBER = 70
BATCH_SIZE = 30
IMAGE_PATH = 'nus_images'
META_PATH = 'nus_wide'

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model_name", "-m", help="name of model", default="att_v1")
    parser.add_argument("--d_name", "-d", help="name of dataset", default="nus"),
    parser.add_argument("--epoch", "-e", help="max epoch", default=20)
    args = parser.parse_args()
    return args.model_name, args.d_name, int(args.epoch)

def main():

    torch.cuda._lazy_init()
    torch.backends.cudnn.benchmark = True
    torch.backends.cudnn.deterministic = True
    torch.autograd.set_detect_anomaly(True)
    torch.manual_seed(2020)
    torch.cuda.manual_seed(2020)
    np.random.seed(2020)
    random.seed(2020)
    dist.init_process_group(backend='nccl')

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model_name, d_name, max_epoch = parse_args()
    f_name = d_name
    print(f"found device {device}")

    if d_name == "coco":
        num_classes = 80
    elif d_name == "nus_small":
        num_classes = 27
    else:
        num_classes = 81

    print(f"Model to use {model_name}, csv output file {f_name}: dataset name: {d_name}: num classes {num_classes}: training {max_epoch}")

    if model_name == "att_v1":
        model = AttModelV1(
            num_classes
        )
        find_params = True

    if model_name == "att_v2":
        model = AttModelV2(
            num_classes
        )
        find_params = False
    if model_name == "resnet_18":
        model = get_resnet_18(num_classes)
        find_params = False

    model.to(device)

    model = DDP(
        model,
        find_unused_parameters=find_params
    )

    print(f"Attaching model {model_name} to device: {device}")
    print(f"Device count: {torch.cuda.device_count()}")

    criterion = nn.BCEWithLogitsLoss()
    optimizer = ZeroRedundancyOptimizer(
        model.parameters(),
        optimizer_class=torch.optim.Adam,
        lr=LEARNING_RATE,
        weight_decay=WEIGHT_DECAY
    )

    scaler = GradScaler()
    print(f"Criterion {criterion} setup, optimizer {optimizer} setup")
    if d_name == "nus":
        train_dataloader, test_dataloader = load_data_nus('train.json', 'test.json')
    if d_name == "nus_small":
        train_dataloader, test_dataloader = load_data_nus('small_train.json', 'small_test.json')
    if d_name == "coco":
        train_dataloader, test_dataloader = load_data_coco()

    batch_losses = []
    batch_losses_test = []
    for i in range(0, max_epoch):
        model.train()
        with tqdm(train_dataloader, unit="batch") as train_epoch:
            train_epoch.set_description(f"Epoch: {i}")
            for imgs, targets in train_epoch:
                imgs, targets = imgs.to(device), targets.to(device)
                optimizer.zero_grad()
                with autocast():
                    model_result = model(imgs)
                    loss = criterion(model_result, targets.float())
                batch_loss_value = loss.item()
                scaler.scale(loss).backward()
                scaler.step(optimizer)
                scaler.update()
                with torch.no_grad():
                    result = calculate_metrics(
                        model_result.cpu().numpy(),
                        targets.cpu().numpy(),
                        num_classes
                )

                result['epoch'] = i
                result['losses'] = batch_loss_value
                batch_losses.append(result)
                train_epoch.set_postfix(train_loss=batch_loss_value, train_acc=result['accuracy'], mAP=result['mAP'])

        with tqdm(test_dataloader, unit="batch") as test_epoch:
            test_epoch.set_description(f"Epoch: {i}")
            with torch.no_grad():
                model.eval()
                for val_imgs, val_targets in test_epoch:
                    val_imgs, val_targets = val_imgs.to(device), val_targets.to(device)
                    with autocast():
                        val_result = model(val_imgs)
                        val_losses = criterion(val_result, val_targets.float())
                    val_metrics = calculate_metrics(
                        val_result.cpu().numpy(),
                        val_targets.cpu().numpy(),
                        num_classes
                    )

                    batch_loss_test = val_losses.item()

                    val_metrics['epoch'] = i
                    val_metrics['losses'] = batch_loss_test
                    batch_losses_test.append(val_metrics)
                    test_epoch.set_postfix(test_loss=batch_loss_test, test_acc=val_metrics['accuracy'], mAP=val_metrics['mAP'])

    time_in_hours = datetime.now().strftime("%Y_%m_%d_%H")
    time_in_minutes = datetime.now().strftime("%H_%M")
    df = pd.DataFrame(batch_losses)
    df_val = pd.DataFrame(batch_losses_test)

    results_directory = f"results_{time_in_hours}"

    if not os.path.exists(results_directory):
        os.makedirs(results_directory)

    print(f"Saving results to {results_directory}")
    df.to_csv(f"{results_directory}/{f_name}_{d_name}_training_{time_in_minutes}.csv")
    df_val.to_csv(f"{results_directory}/{f_name}_{d_name}_validation_{time_in_minutes}.csv")
    torch.save(model, f"{results_directory}/{f_name}_{d_name}_model_{time_in_minutes}")

if __name__ == "__main__":
    main()