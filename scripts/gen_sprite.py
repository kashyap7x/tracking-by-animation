import os
import os.path as path
import argparse
import subprocess
from joblib import Parallel, delayed
import multiprocessing
import math
import numpy as np
import matplotlib.pyplot as plt
import torch
import torch.nn as nn
import sys
sys.path.append("modules")
import utils


torch.manual_seed(0)
parser = argparse.ArgumentParser()
parser.add_argument('--v', type=int, default=0)
parser.add_argument('--metric', type=int, default=1)
arg = parser.parse_args()


N = 1 if arg.metric == 1 else 64
T = 10
H = 128
W = 128
D = 3
h = 21
w = 21
O = 3
frame_num = 1e4 if arg.metric == 1 else 1e5
train_ratio = 0 if arg.metric == 1 else 0.96
birth_prob = 0.5
appear_interval = 5
scale_var = 0.1
ratio_var = 0.2
velocity = 5.3
task = 'spmot'
m = h // 2
eps = 1e-5

txt_name = task + 'gt.txt'
metric_dir = 'metric' if arg.metric == 1 else ''
output_dir = path.join('data', task, 'pt', metric_dir)
output_input_dir = path.join(output_dir, 'input')
output_gt_dir = path.join(output_dir, 'gt')
if arg.v == 0:
    utils.rmdir(output_input_dir); utils.mkdir(output_input_dir)
    utils.rmdir(output_gt_dir); utils.mkdir(output_gt_dir)


# color template
color_num = 6
color_temp = torch.ByteTensor(color_num, 1, 1, D).zero_()
for i in range(0, color_num):
    R = math.floor((i+1)/4) * 255
    G = math.floor(((i+1)%4)/2) * 255
    B = (i+1)%2 * 255
    color_temp[i, :, :, 2].fill_(R)
    color_temp[i, :, :, 1].fill_(G)
    color_temp[i, :, :, 0].fill_(B)

# shape template
shape_num = 4
shape_temp = torch.ByteTensor(shape_num, h, w).zero_()
# circle
circle = shape_temp[0]
center = (h - 1) / 2
radius = h / 2
for i in range(0, h):
    for j in range(0, w):
        if math.pow(i - center, 2) + math.pow(j - center, 2) <= radius * radius:
            circle[i, j] = 255
# rectangle
rectangle = shape_temp[1]
rectangle.fill_(255)
# triangle
triangle = shape_temp[2]
for i in range(0, h):
    for j in range(0, w):
        if j <= w/2 - 1:
            if (h - i) / (j + 1) <= h / (w / 2):
                triangle[i, j] = 255
        else:
            if (h - i) / (w - j) <= h / (w / 2):
                triangle[i, j] = 255
# diamond
diamond = shape_temp[3]
for i in range(0, h):
    for j in range(0, w):
        if math.fabs(i - center) + math.fabs(j - center) <= radius:
            diamond[i, j] = 255

# generate data from trackers
train_frame_num = frame_num * train_ratio
test_frame_num = frame_num * (1 - train_ratio)
print('train frame number: ' + str(train_frame_num))
print('test frame number: ' + str(test_frame_num))
batch_nums = {
    'train': math.floor(train_frame_num / (N * T)),
    'test': math.floor(test_frame_num / (N * T))
}


core_num = 1 if arg.metric == 1 else multiprocessing.cpu_count()
oid = 0 # object id
print("Running with " + str(core_num) + " cores.")
if arg.metric == 1:
    utils.mkdir(output_gt_dir)
    file = open(path.join(output_dir, txt_name), "w")
def process_batch(states, batch_id):
    global oid
    buffer_big = torch.ByteTensor(2, H + 2 * h, W + 2 * w, D).zero_()
    org_seq = torch.ByteTensor(T, H, W, D).zero_()
    # sample all the random variables
    unif = torch.rand(T, O)
    color_id = torch.rand(T, O).mul_(color_num).floor_().long()
    shape_id = torch.rand(T, O).mul_(shape_num).floor_().long()
    direction_id = torch.rand(T, O).mul_(4).floor_().long() # [0, 3]
    position_id = torch.rand(T, O, 2).mul_(H-2*m).add_(m).floor_().long() # [m, H-m-1]
    scales = torch.rand(T, O).mul_(2).add_(-1).mul_(scale_var).add_(1) # [1 - var, 1 + var]
    ratios = torch.rand(T, O).mul_(2).add_(-1).mul_(ratio_var).add_(1).sqrt_() # [sqrt(1 - var), sqrt(1 + var)]
    frames = []
    for t in range(0, T):
        tao = batch_id * T + t
        buffer_label = torch.FloatTensor(H, W, O).zero_()
        for o in range(0, O):
            if states[o][0] < appear_interval: # wait for interval frames
                states[o][0] = states[o][0] + 1
            elif states[o][0] == appear_interval: # allow birth
                if unif[t][o].item() < birth_prob: # birth
                    # shape and appearance
                    color = color_id[t][o].item()
                    shape = shape_id[t][o].item()
                    scale = scales[t][o].item()
                    ratio = ratios[t][o].item()
                    h_, w_ = round(h * scale * ratio), round(w * scale / ratio)
                    color_patch = torch.ByteTensor(h_, w_, D).fill_(1) * color_temp[color]
                    shape_patch = utils.imresize(shape_temp[shape], h_, w_)
                    # pose
                    direction = direction_id[t][o].item()
                    position = position_id[t][o]
                    x1, y1, x2, y2 = None, None, None, None
                    if direction == 0:
                        x1 = position[0].item()
                        y1 = m
                        x2 = position[1].item()
                        y2 = H - 1 - m
                    elif direction == 1:
                        x1 = position[0].item()
                        y1 = H - 1 - m
                        x2 = position[1].item()
                        y2 = m
                    elif direction == 2:
                        x1 = m
                        y1 = position[0].item()
                        x2 = W - 1 - m
                        y2 = position[1].item()
                    else:
                        x1 = W - 1 - m
                        y1 = position[0].item()
                        x2 = m
                        y2 = position[1].item()
                    theta = math.atan2(y2 - y1, x2 - x1)
                    vx = velocity * math.cos(theta)
                    vy = velocity * math.sin(theta)
                    # initial states
                    states[o] = [appear_interval + 1, color_patch, shape_patch, x1, y1, vx, vy, 0, oid]
                    oid += 1
            else:  # exists
                color_patch = states[o][1]
                shape_patch = states[o][2]
                x1, y1, vx, vy = states[o][3], states[o][4], states[o][5], states[o][6]
                step = states[o][7]
                x = round(x1 + step * vx)
                y = round(y1 + step * vy)
                if x < m-eps or x > W-1-m+eps or y < m-eps or y > H-1-m+eps: # the object disappears
                    states[o][0] = 0
                else:
                    h_, w_ = color_patch.size(0), color_patch.size(1)
                    # center and start position for the big image
                    center_x = x + w
                    center_y = y + h
                    top = math.floor(center_y - (h_ - 1) / 2)
                    left = math.floor(center_x - (w_ - 1) / 2)
                    # put the color patch on image
                    color_img = buffer_big[0].zero_()
                    color_img.narrow(0, top, h_).narrow(1, left, w_).copy_(color_patch)
                    color_img = color_img.narrow(0, h, H).narrow(1, w, W) # H * W * D
                    # put the shape patch on image
                    shape_img = buffer_big[1, :, :, 0].zero_()
                    shape_img.narrow(0, top, h_).narrow(1, left, w_).copy_(shape_patch)
                    shape_img = shape_img.narrow(0, h, H).narrow(1, w, W).unsqueeze(2) # H * W * 1
                    # convert to float
                    color_img_f = color_img.float()
                    shape_img_f = shape_img.float()
                    # synthesize a frame
                    org_img_f = org_seq[t].float() # H * W * D
                    syn_image = org_img_f + shape_img_f/255 * (color_img_f - org_img_f)
                    buffer_label[:,:,o] = shape_img_f[:,:,0]/255
                    for i in range(0, o):
                        buffer_label[:,:,i] *= 1-shape_img_f[:,:,0]/255
                    org_seq[t].copy_(syn_image.round().byte())
                    # update the position
                    states[o][7] = states[o][7] + 1
                    # save for metric evaluation
                    if arg.metric == 1:
                        file.write("%d,%d,%.3f,%.3f,%.3f,%.3f,1,-1,-1,-1\n" %
                            (batch_id*T+t+1, states[o][8]+1, left-w+1, top-h+1, w_, h_))

        if arg.metric == 1:
            frame = {}
            frame['timestamp'] = int(t)
            frame['num'] = int(tao)
            frame['class'] = 'frame'

            annotations = []

            for j in range(0,O):
                bin_img = nn.functional.interpolate(buffer_label[:,:,j].reshape(1,1,H,W), scale_factor=0.5)
                bin_img = (bin_img!=0).numpy().astype(np.int)
                if bin_img.sum() == 0:
                    continue
                else:
                    annotation = {
                        'mask': utils.rle_encode(bin_img),
                        'id': int(O * batch_id + j),
                    }
                    annotations.append(annotation)
            frame['annotations'] = annotations
            frames.append(frame)

    return org_seq, states, frames


states_batch = []
videos = []
for n in range(0, N):
    states_batch.append([])
    for o in range(0, O):
        states_batch[n].append([0]) # the states of the o-th object in the n-th sample
with Parallel(n_jobs=core_num, backend="threading") as parallel:
    for split in ['train', 'test']:
        S = batch_nums[split]
        for s in range(0, S): # for each batch of sequences
            video = {}
            video['class'] = 'video'
            video['filename'] = 'video_id_{}'.format(s)
            out_batch = parallel(delayed(process_batch)(states_batch[n], s) for n in range(0, N)) # N * 2 * T * H * W * D
            out_batch = list(zip(*out_batch)) # 2 * N * T * H * W * D
            org_seq_batch = torch.stack(out_batch[0], dim=0) # N * T * H * W * D
            states_batch = out_batch[1] # N * []
            frames = out_batch[2][0]
            video['frames'] = frames
            videos.append(video)
            if arg.v == 1:
                for t in range(0, T):
                    utils.imshow(org_seq_batch[0, t], 400, 400, 'img', 50)
            else:
                org_seq_batch = org_seq_batch.permute(0, 1, 4, 2, 3) # N * T * D * H * W
                filename = split + '_' + str(s) + '.pt'
                torch.save(org_seq_batch, path.join(output_input_dir, filename))

            print(split + ': ' + str(s+1) + ' / ' + str(S))
if arg.metric == 1:
    file.close()
    utils.save_json(videos, path.join(output_gt_dir, 'spmot_test_gt_mot_annotations_masks.json'))

# save the data configuration
data_config = {
    'task': task,
    'train_batch_num': batch_nums['train'],
    'test_batch_num': batch_nums['test'],
    'N': N,
    'T': T,
    'D': D,
    'H': H,
    'W': W,
    'h': h,
    'w': w,
    'zeta_s': scale_var,
    'zeta_r': [1, ratio_var]
}
utils.save_json(data_config, path.join(output_dir, 'data_config.json'))
