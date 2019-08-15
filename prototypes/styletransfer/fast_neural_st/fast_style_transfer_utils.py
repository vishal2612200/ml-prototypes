from __future__ import print_function, division
import os, mimetypes
import sys
import torch
import pandas as pd
from collections import defaultdict
import PIL
import random
from tqdm.autonotebook import tqdm
from tqdm import tnrange
import numpy as np
import matplotlib.pyplot as plt
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms, utils, models
from pathlib import Path
from random import shuffle
from scipy import ndimage
#from torchsummary import summary
import torch.nn as nn
import time
import copy
import torch.optim as optim
from torch.optim import lr_scheduler

imagenet_stats = ([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
if sys.platform == 'linux':
    path = Path('/home/ec2-user/SageMaker/data')
else:
    path = Path('C:/Users/francesco.pochetti/Downloads/imagenette')
    
#################################################
# UNIT TESTS
#################################################
    
def test_data(dl, ds, bs, size):
    assert dl['train'].dataset == ds['train']
    assert dl['valid'].dataset == ds['valid']
    
    assert abs(len(ds['train'])/bs - len(dl['train'])) < 2
    assert abs(len(ds['valid'])/bs - len(dl['valid'])) < 2
    
    i,c,s = next(iter(dl['train']))
    assert i.shape[0] == bs
    assert i.shape[1] == 3
    assert i.shape[2] == i.shape[3] == (size+35*2) 

    i,c,s = next(iter(dl['valid']))
    assert i.shape[0] == bs
    assert i.shape[1] == 3
    assert i.shape[2] == i.shape[3] == (size+35*2) 

def test_deprocess(ds_item):
    denorm = DeProcess(imagenet_stats)
    d = denorm(ds_item)
    return PIL.Image.fromarray(d[random.choice([0,1,2])])    

def test_hooks(model, dl, bs):
    fst = FastStyleTransfer(dl, *get_model_opt(model))
    assert fst.hooks_initialized == True
    d = random.choice(['train', 'valid'])
    i, c, s = next(iter(fst.dl[d]))
    i = i.to(fst.device)
    c = c.to(fst.device)
    s = s.to(fst.device)
    assert torch.allclose(i, c)
    assert torch.allclose(i, s) == False

    fst.vgg(i)
    input_act = [o.features.clone().detach_().to(fst.device) for o in fst.act]
    fst.vgg(c)
    content_act = [o.features.clone().detach_().to(fst.device) for o in fst.act]
    fst.vgg(s)
    style_act = [o.features.clone().detach_().to(fst.device) for o in fst.act]

    assert len(input_act) == len(content_act) == len(style_act) == 5
    assert torch.allclose(input_act[1], content_act[1])
    assert torch.allclose(input_act[3], content_act[3])
    assert style_act[0].shape[0] == style_act[2].shape[0] == style_act[4].shape[0] == bs
    fst.close_hooks()
    assert fst.hooks_initialized == False
        
def test_losses(model, dl):
    fst = FastStyleTransfer(dl, *get_model_opt(model))
    assert fst.hooks_initialized == True
    d = random.choice(['train', 'valid'])
    i, c, s = next(iter(fst.dl[d]))
    i = i.to(fst.device)
    c = c.to(fst.device)
    s = s.to(fst.device)
    assert torch.allclose(i, c)
    assert torch.allclose(i, s) == False

    fst.vgg(i)
    input_act = [o.features.clone().detach_().to(fst.device) for o in fst.act]
    fst.vgg(c)
    content_act = [o.features.clone().detach_().to(fst.device) for o in fst.act]
    fst.vgg(s)
    style_act = [o.features.clone().detach_().to(fst.device) for o in fst.act]
    
    co_loss = fst.content_mse(input_act[0], content_act[0])
    assert isinstance(co_loss, torch.Tensor)
    
    st_loss = fst.gram_mse_loss(input_act[4], style_act[4])
    assert isinstance(st_loss, torch.Tensor)
    
    fst.input_act = input_act
    fst.content_act = content_act
    fst.style_act = style_act
    loss, content, style = fst.combined_loss()
    assert isinstance(st_loss, torch.Tensor)
    fst.close_hooks()
    assert fst.hooks_initialized == False

#################################################    
# UTILS
#################################################

def _get_files(p, fs, extensions=None):
    p = Path(p)
    res = [p/f for f in fs if not f.startswith('.')
           and ((not extensions) or f'.{f.split(".")[-1].lower()}' in extensions)]
    return res
                
def get_files(path, extensions=None, recurse=False, include=None):
    path = Path(path)
    extensions = {e.lower() for e in extensions}
    if recurse:
        res = []
        for i,(p,d,f) in enumerate(os.walk(path)): # returns (dirpath, dirnames, filenames)
            if include is not None and i==0: d[:] = [o for o in d if o in include]
            else:                            d[:] = [o for o in d if not o.startswith('.')]
            res += _get_files(p, f, extensions)
        return res
    else:
        f = [o.name for o in os.scandir(path) if o.is_file()]
        return _get_files(path, f, extensions)

class SaveFeatures():
    features=None
    
    def __init__(self, m): 
        self.hook = m.register_forward_hook(self.hook_fn)
    
    def hook_fn(self, module, input, output): 
        self.features = output
    
    def close(self): 
        self.hook.remove()

def gram(input):
    b,c,h,w = input.size()
    x = input.view(b*c, -1)
    return torch.mm(x, x.t())/input.numel() #*1e6
    
def content2style(dataloaders): # NOT WORKING! TO BE REFACTORED
    activations = [SaveFeatures(list(vgg.features)[idx]) for idx in convs]
    content = 0
    style = 0
    for i, (inputs, contents, styles) in enumerate(dataloaders['train']):
        if i == 4: break
        
        inputs = inputs.to(device)
        contents = contents.to(device)
        styles = styles.to(device)

        vgg(contents)
        target_cont = [o.features.clone().detach_().to(device) for o in activations]

        vgg(styles)
        target_style = [o.features.clone().detach_().to(device) for o in activations]

        outputs = model(inputs)
        vgg(outputs)
        opt_cat = [o.features.clone().to(device) for o in activations]

        _, content_loss, style_loss = combined_loss(opt_cat, target_style, target_cont)
        
        content += content_loss.cpu().detach().numpy()
        style += style_loss.cpu().detach().numpy()
    
    for sf in activations: sf.close()
    return content/(style+1)

#################################################
# DATASETS & DATALOADERS
#################################################

class StyleTransferDataset(Dataset):
    """Style Transfer dataset."""

    def __init__(self, csv_file, train_test, transform=None, sample=None):
        data = pd.read_csv(csv_file)
        if sample: data = data.sample(int(len(data)*sample))
        self.train_test = train_test
        data.loc[:,['content_', 'style_']] = data.loc[:,['content_', 'style_']].applymap(lambda x: Path(x))
        self.data = data.loc[data.split_==train_test,:].reset_index(drop=True)
        self.transform = transform

    def __len__(self):
        return len(self.data)
    
    def __repr__(self):
        item = self.__getitem__(0)
        
        _1 = f'{self.train_test.capitalize()} dataset: {len(self.data)} items\n'
        _2 = f'Item: {type(item)} of {len(item)} {type(item[0])}\n'
        _3 = f"Item example: 'input':{ item[0].shape},'content':{item[1].shape},'style':{item[2].shape}"

        return _1+_2+_3
    
    def __getitem__(self, idx):
        if type(idx) == torch.Tensor:
            idx = idx.item()
        
        content_img = self.data.content_.iloc[idx]
        content_img = PIL.Image.open(content_img)
        
        style_img = self.data.style_.iloc[idx]
        style_img = PIL.Image.open(style_img)
        
        #opt_img = np.random.uniform(0, 1, size=(content_img.size + (3,))).astype(np.float32)
        #opt_img = ndimage.filters.median_filter(opt_img, [8,8,1])
        #item = {'input': content_img, #PIL.Image.fromarray(np.uint8(opt_img*255)),
        #        'content': content_img, 
        #        'style': style_img}
        
        item = {'content': content_img, 
                'style': style_img}

        
        if self.transform: item = compose(item, self.transform)

        return item['content'], item['content'], item['style']
    
def compose(x, funcs, *args, order_key='_order', **kwargs):
    key = lambda o: getattr(o, order_key, 0)
    for f in sorted(list(funcs), key=key): x = f(x, **kwargs)
    return x

class Transform(): _order=0
        
class MakeRGB(Transform):
    def __call__(self, item): return {k: v.convert('RGB') for k, v in item.items()}

class ResizeFixed(Transform):
    _order=10
    def __init__(self, size):
        if isinstance(size,int): size=(size,size)
        self.size = size
        
    def __call__(self, item): return {k: v.resize(self.size, PIL.Image.BILINEAR) for k, v in item.items()}

class ToByteTensor(Transform):
    _order=20
    def to_byte_tensor(self, item):
        res = torch.ByteTensor(torch.ByteStorage.from_buffer(item.tobytes()))
        w,h = item.size
        return res.view(h,w,-1).permute(2,0,1)
    
    def __call__(self, item): return {k: self.to_byte_tensor(v) for k, v in item.items()}


class ToFloatTensor(Transform):
    _order=30
    def to_float_tensor(self, item): return item.float().div_(255.)
    
    def __call__(self, item): return {k: self.to_float_tensor(v) for k, v in item.items()}
    
class Normalize(Transform):
    _order=40
    def __init__(self, stats):
        self.mean = torch.as_tensor(stats[0] , dtype=torch.float32)
        self.std = torch.as_tensor(stats[1] , dtype=torch.float32)
    
    def normalize(self, item): return item.sub_(self.mean[:, None, None]).div_(self.std[:, None, None])
    
    def __call__(self, item): return {k: nn.functional.pad(self.normalize(v), (35,35), 'constant') for k, v in item.items()}
    
class PilRandomDihedral(Transform):
    _order=15
    def __init__(self, p=0.75): self.p=p*7/8 #Little hack to get the 1/8 identity dihedral transform taken into account.
    
    def __call__(self, item):
        if random.random()>self.p: return item
        return {k: v.transpose(random.randint(0,6)) for k, v in item.items()}
    
class DeProcess(Transform):
    _order=50
    def __init__(self, stats):
        self.mean = torch.as_tensor(stats[0] , dtype=torch.float32)
        self.std = torch.as_tensor(stats[1] , dtype=torch.float32)
    
    def de_normalize(self, item): return (item*self.std[:, None, None]+self.mean[:, None, None])*255.
    def rearrange_axis(self, item): return np.moveaxis(item, 0, -1)
    def to_np(self, item): return np.uint8(np.array(item))
    def de_process(self, item): return self.rearrange_axis(self.to_np(self.de_normalize(item)))
    
    def __call__(self, item): 
        if isinstance(item, torch.Tensor): return self.de_process(item) 
        if isinstance(item, tuple): return tuple([self.de_process(v) for v in item])
        if isinstance(item, dict): return {k: self.de_process(v) for k, v in item.items()}
        
#################################################
# RESNET UNET
#################################################

def convrelu(in_channels, out_channels, kernel, padding):
    return nn.Sequential(
        nn.Conv2d(in_channels, out_channels, kernel, padding=padding),
        nn.ReLU(inplace=True),
    )

class ResNetUNet(nn.Module):
    def __init__(self, n_class=3):
        super().__init__()

        self.base_model = models.resnet18(pretrained=True)
        self.base_layers = list(self.base_model.children())

        self.layer0 = nn.Sequential(*self.base_layers[:3]) # size=(N, 64, x.H/2, x.W/2)
        self.layer0_1x1 = convrelu(64, 64, 1, 0)
        self.layer1 = nn.Sequential(*self.base_layers[3:5]) # size=(N, 64, x.H/4, x.W/4)
        self.layer1_1x1 = convrelu(64, 64, 1, 0)
        self.layer2 = self.base_layers[5]  # size=(N, 128, x.H/8, x.W/8)
        self.layer2_1x1 = convrelu(128, 128, 1, 0)
        self.layer3 = self.base_layers[6]  # size=(N, 256, x.H/16, x.W/16)
        self.layer3_1x1 = convrelu(256, 256, 1, 0)
        self.layer4 = self.base_layers[7]  # size=(N, 512, x.H/32, x.W/32)
        self.layer4_1x1 = convrelu(512, 512, 1, 0)

        self.upsample = nn.Upsample(scale_factor=2, mode='bilinear', align_corners=True)

        self.conv_up3 = convrelu(256 + 512, 512, 3, 1)
        self.conv_up2 = convrelu(128 + 512, 256, 3, 1)
        self.conv_up1 = convrelu(64 + 256, 256, 3, 1)
        self.conv_up0 = convrelu(64 + 256, 128, 3, 1)

        self.conv_original_size0 = convrelu(3, 64, 3, 1)
        self.conv_original_size1 = convrelu(64, 64, 3, 1)
        self.conv_original_size2 = convrelu(64 + 128, 64, 3, 1)

        self.conv_last = nn.Conv2d(64, n_class, 1)

    def forward(self, input):
        x_original = self.conv_original_size0(input)
        
        x_original = self.conv_original_size1(x_original)

        layer0 = self.layer0(input)
        layer1 = self.layer1(layer0)
        layer2 = self.layer2(layer1)
        layer3 = self.layer3(layer2)
        layer4 = self.layer4(layer3)

        layer4 = self.layer4_1x1(layer4)
        x = self.upsample(layer4)
        layer3 = self.layer3_1x1(layer3)
        x = torch.cat([x, layer3], dim=1)
        x = self.conv_up3(x)

        x = self.upsample(x)
        layer2 = self.layer2_1x1(layer2)
        x = torch.cat([x, layer2], dim=1)
        x = self.conv_up2(x)

        x = self.upsample(x)
        layer1 = self.layer1_1x1(layer1)
        x = torch.cat([x, layer1], dim=1)
        x = self.conv_up1(x)

        x = self.upsample(x)
        layer0 = self.layer0_1x1(layer0)
        x = torch.cat([x, layer0], dim=1)
        x = self.conv_up0(x)

        x = self.upsample(x)
        x = torch.cat([x, x_original], dim=1)
        x = self.conv_original_size2(x)

        out = self.conv_last(x)

        return out        

#################################################
# TRANSFORMER NET
#################################################

class TransformerNet(torch.nn.Module):
    def __init__(self):
        super().__init__()
        # Initial convolution layers
        self.conv1 = ConvLayer(3, 32, kernel_size=9, stride=1)
        self.in1 = torch.nn.InstanceNorm2d(32, affine=True)
        self.conv2 = ConvLayer(32, 64, kernel_size=3, stride=2)
        self.in2 = torch.nn.InstanceNorm2d(64, affine=True)
        self.conv3 = ConvLayer(64, 128, kernel_size=3, stride=2)
        self.in3 = torch.nn.InstanceNorm2d(128, affine=True)
        # Residual layers
        self.res1 = ResidualBlock(128)
        self.res2 = ResidualBlock(128)
        self.res3 = ResidualBlock(128)
        self.res4 = ResidualBlock(128)
        self.res5 = ResidualBlock(128)
        # Upsampling Layers
        self.deconv1 = UpsampleConvLayer(128, 64, kernel_size=3, stride=1, upsample=2)
        self.in4 = torch.nn.InstanceNorm2d(64, affine=True)
        self.deconv2 = UpsampleConvLayer(64, 32, kernel_size=3, stride=1, upsample=2)
        self.in5 = torch.nn.InstanceNorm2d(32, affine=True)
        self.deconv3 = ConvLayer(32, 3, kernel_size=9, stride=1)
        # Non-linearities
        self.relu = torch.nn.ReLU()

    def forward(self, X):
        y = self.relu(self.in1(self.conv1(X)))
        y = self.relu(self.in2(self.conv2(y)))
        y = self.relu(self.in3(self.conv3(y)))
        y = self.res1(y)
        y = self.res2(y)
        y = self.res3(y)
        y = self.res4(y)
        y = self.res5(y)
        y = self.relu(self.in4(self.deconv1(y)))
        y = self.relu(self.in5(self.deconv2(y)))
        y = self.deconv3(y)
        return y

class ConvLayer(torch.nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size, stride):
        super(ConvLayer, self).__init__()
        reflection_padding = kernel_size // 2
        self.reflection_pad = torch.nn.ReflectionPad2d(reflection_padding)
        self.conv2d = torch.nn.Conv2d(in_channels, out_channels, kernel_size, stride)

    def forward(self, x):
        out = self.reflection_pad(x)
        out = self.conv2d(out)
        return out

class ResidualBlock(torch.nn.Module):
    """ResidualBlock
    introduced in: https://arxiv.org/abs/1512.03385
    recommended architecture: http://torch.ch/blog/2016/02/04/resnets.html
    """

    def __init__(self, channels):
        super(ResidualBlock, self).__init__()
        self.conv1 = ConvLayer(channels, channels, kernel_size=3, stride=1)
        self.in1 = torch.nn.InstanceNorm2d(channels, affine=True)
        self.conv2 = ConvLayer(channels, channels, kernel_size=3, stride=1)
        self.in2 = torch.nn.InstanceNorm2d(channels, affine=True)
        self.relu = torch.nn.ReLU()

    def forward(self, x):
        residual = x
        out = self.relu(self.in1(self.conv1(x)))
        out = self.in2(self.conv2(out))
        out = out + residual
        return out

class UpsampleConvLayer(torch.nn.Module):
    """UpsampleConvLayer
    Upsamples the input and then does a convolution. This method gives better results
    compared to ConvTranspose2d.
    ref: http://distill.pub/2016/deconv-checkerboard/
    """

    def __init__(self, in_channels, out_channels, kernel_size, stride, upsample=None):
        super(UpsampleConvLayer, self).__init__()
        self.upsample = upsample
        reflection_padding = kernel_size // 2
        self.reflection_pad = torch.nn.ReflectionPad2d(reflection_padding)
        self.conv2d = torch.nn.Conv2d(in_channels, out_channels, kernel_size, stride)

    def forward(self, x):
        x_in = x
        if self.upsample:
            x_in = torch.nn.functional.interpolate(x_in, mode='nearest', scale_factor=self.upsample)
        out = self.reflection_pad(x_in)
        out = self.conv2d(out)
        return out
    
#################################################
# FAST STYLE TRANSFER CLASS
#################################################

class FastStyleTransfer():
    def __init__(self, dl, model, opt, sched=None, style_weight=1e10, content_weight=1e5, tv_weight=None):
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        self.mseloss = nn.MSELoss()
        self.init_vgg()
        self.convs = [i-2 for i,o in enumerate(list(self.vgg.features)) if isinstance(o,nn.MaxPool2d)]
        self.model = model.to(self.device)
        self.original_model = model.to(self.device)
        self.opt = opt
        self.sched = sched
        self.content_weight = content_weight
        self.style_weight = style_weight
        self.tv_weight = tv_weight
        self.dl = dl
        self.initialize_hooks()
        self.style_act = None
        self.training_done = False
        
    def init_vgg(self):
        self.vgg = models.vgg16(pretrained=True).to(self.device)
        self.vgg.eval()
    
    def reinitialize_unet(self): self.model = copy.deepcopy(self.original_model)
        
    def initialize_hooks(self): 
        self.act = [SaveFeatures(list(self.vgg.features)[idx]) for idx in self.convs]
        self.hooks_initialized = True
    
    def close_hooks(self): 
        for hook in self.act: hook.close()
        self.hooks_initialized = False
        
    def vgg_conv_layers(self): return np.array(list(self.vgg.features))[self.convs]
    
    def content_mse(self, input, target): return self.mseloss(input, target) #*1e3

    def gram_mse_loss(self, input, target): return self.mseloss(gram(input), gram(target))

    def tv_loss(self):
        l = (torch.sum(torch.abs(self.inputs[:, :, :, :-1] - self.inputs[:, :, :, 1:])) + 
             torch.sum(torch.abs(self.inputs[:, :, :-1, :] - self.inputs[:, :, 1:, :])))
        return l
    
    def combined_loss(self, c2s=1.0):
        style_losses = [self.gram_mse_loss(o, s) for o,s in zip(self.input_act, self.style_act)]
        
        #content_losses = [content_mse(o, s) for o,s in zip(opt_cat, target_cont)]
        content_losses = [self.content_mse(self.input_act[2], self.content_act[2])]
        
        style = sum(style_losses) * c2s * self.style_weight
        content = sum(content_losses) * self.content_weight
        loss = content + style
        if self.tv_weight is not None: loss += self.tv_loss() * self.tv_weight
        return loss, content, style
    
    def store_metrics(self, phase, epoch, i):
        self.metrics[phase]['epoch'] += [epoch]
        self.metrics[phase]['batch'] += [i]
        self.metrics[phase]['batch_size'] += [self.inputs.size(0)]
        self.metrics[phase]['total_loss'] += [self.loss.cpu().detach().numpy()]
        self.metrics[phase]['content_loss'] += [self.content_loss.cpu().detach().numpy()]
        self.metrics[phase]['style_loss'] += [self.style_loss.cpu().detach().numpy()]
        
    def get_epoch_loss(self, phase):
        d = pd.DataFrame(self.metrics[phase])
        d = d.groupby('epoch')['total_loss','batch_size'].apply(lambda x : x.sum()). \
            reset_index().sort_values(by='epoch').tail(1)   
        d = d.total_loss/d.batch_size
        return np.array(d)[0]
    
    def get_metrics(self, phase):
        df = pd.DataFrame(self.metrics[phase])
        df['total'] = df.total_loss/df.batch_size
        df['content'] = df.content_loss/df.batch_size
        df['style'] = df.style_loss/df.batch_size
        return df

    def plot_losses(self, phase, group=20):
        df = self.get_metrics(phase)
        df[f'{group}-batch-average'] = df.batch // group
        df = df.groupby(f'{group}-batch-average')['total','content','style'].sum().reset_index()
        m = df.total.mean()
        s = df.total.std()
        df['outlier'] = np.where(df.total < (m+3*s), False, True)
        
        x_axis = min(18, int(0.03*len(df)))
        fig, ax = plt.subplots(figsize=(x_axis, 5))
        df.loc[df.outlier==False,:].plot(ax=ax, x=f'{group}-batch-average', y=['total', 'content', 'style'])
        plt.show()

    def run_st(self, tensor):
        out = self.model(tensor[None].to(self.device))
        return out[0].detach().cpu()
    
    def plot_samples(self, phase):
        ds = self.dl[phase].dataset
        idx = random.sample(range(len(ds)), 3)
        denorm = DeProcess(imagenet_stats)
        items = [denorm((self.run_st(ds[i][0]), *ds[i][1:])) for i in idx]

        fig, axes = plt.subplots(3,3, figsize=(8,8))
        for i in range(3):
            for j in range(3):
                ax = axes[i, j]
                ax.imshow(items[i][j])
                ax.axis('off')
        plt.subplots_adjust(wspace=0.01, hspace=0.01)
        plt.show()
    
    def train(self, num_epochs=1, print_every=1000, plot=False):
        if not self.hooks_initialized: self.initialize_hooks()
        self.metrics =  defaultdict(lambda: defaultdict(list))
        #c2s = content2style(dataloaders)

        for epoch in tnrange(num_epochs, desc='Epoch'):
            since = time.time()

            for phase in ['train', 'valid']:
                print(f'\nPhase: {phase}')
                if phase == 'train':
                    if self.sched is not None:
                        self.sched.step()
                        for param_group in self.opt.param_groups:
                            print("LR", param_group['lr'])
                    
                    self.model.train() 
                else:
                    self.model.eval()
                    save_model_filename = ["epoch", str(epoch),  
                                           str(time.ctime()).replace(' ', '_'), 
                                           str(self.content_weight),
                                           str(self.style_weight) + ".model"]
                    torch.save(self.model.state_dict(), '_'.join(save_model_filename))

                if self.style_act is not None: self.style_act = None
                progress = tqdm(enumerate(self.dl[phase]), desc="Loss: ", total=len(self.dl[phase]))
                
                for i, (inputs, contents, styles) in progress:
                    self.inputs = inputs.to(self.device)
                    contents = contents.to(self.device)
                    styles = styles.to(self.device)
                    if i % print_every == 0: print(f'batch: {i}: (input, content, style) = {self.inputs.shape}, {contents.shape}, {styles.shape}')

                    self.vgg(contents)
                    self.content_act = [o.features.clone().detach_().to(self.device) for o in self.act]

                    if self.style_act is None or i == (len(self.dl[phase])-1):
                        self.vgg(styles)
                        self.style_act = [o.features.clone().detach_().to(self.device) for o in self.act]

                    self.opt.zero_grad()

                    with torch.set_grad_enabled(phase == 'train'):
                        outputs = self.model(self.inputs)
                        self.vgg(outputs)
                        self.input_act = [o.features.clone().to(self.device) for o in self.act]
                
                        self.loss, self.content_loss, self.style_loss = self.combined_loss()
                        self.store_metrics(phase, epoch, i)
                        
                        if phase == 'train':
                            self.loss.backward()
                            self.opt.step()
                
                epoch_loss = self.get_epoch_loss(phase)
                progress.set_description("Loss: {:.4f}".format(epoch_loss))
                if phase == 'train': 
                    print(f"phase: {phase}, loss: {epoch_loss}")
                    if plot: self.plot_losses(phase)
                if plot: self.plot_samples(phase)

            time_elapsed = time.time() - since
            print('{:.0f}m {:.0f}s'.format(time_elapsed // 60, time_elapsed % 60))

        self.close_hooks()
        self.training_done = True
        return
    
def get_model_opt(model, sched=None):
    unet = model
    optimizer = optim.Adam(filter(lambda p: p.requires_grad, unet.parameters()), lr=1e-3)
    if sched: sched = lr_scheduler.CosineAnnealingLR(optimizer, 50)
    return [unet, optimizer, sched]

#device = torch.device('cpu')
#model = TheModelClass(*args, **kwargs)
#model.load_state_dict(torch.load(PATH, map_location=device))
#https://pytorch.org/tutorials/beginner/saving_loading_models.html