import torch
import torch.nn as nn
import torch.nn.functional as F

import torchvision.models as models


class DenseFeatureExtractionModule(nn.Module):
    def __init__(self, finetune_feature_extraction=False, use_cuda=True, finetune_layers=2, truncated_blocks=2, model_type=None, output_size=0):
        super(DenseFeatureExtractionModule, self).__init__()
        
        if model_type == 'vgg16':
            model = models.vgg16(pretrained=True)
        
            vgg16_layers = [
                'conv1_1', 'relu1_1', 'conv1_2', 'relu1_2',
                'pool1',
                'conv2_1', 'relu2_1', 'conv2_2', 'relu2_2',
                'pool2',
                'conv3_1', 'relu3_1', 'conv3_2', 'relu3_2', 'conv3_3', 'relu3_3',
                'pool3',
                'conv4_1', 'relu4_1', 'conv4_2', 'relu4_2', 'conv4_3', 'relu4_3',
                'pool4',
                'conv5_1', 'relu5_1', 'conv5_2', 'relu5_2', 'conv5_3', 'relu5_3',
                'pool5'
            ]

            if truncated_blocks == 3:
                conv_idx = vgg16_layers.index('conv3_3')
            elif truncated_blocks == 2:
                conv_idx = vgg16_layers.index('conv4_3')
            elif truncated_blocks == 1:
                conv_idx = vgg16_layers.index('conv5_3')
            
            self.model = nn.Sequential(
                *list(model.features.children())[: conv_idx + 1]
            )
            
        elif model_type == 'res50':
            model = models.resnet50(pretrained=True)
            self.model = nn.Sequential(
                *list(model.children())[: -truncated_blocks-1]
            )
            if output_size > 0:
                print(output_size)
                if truncated_blocks == 1:
                    self.model[7][2].conv3 = nn.Conv2d(512, output_size, kernel_size=(1, 1), stride=(1, 1), bias=False)
                    self.model[7][2].bn3 = nn.BatchNorm2d(output_size, eps=1e-05, momentum=0.1, affine=True, track_running_stats=True)
                    self.model[7][0].downsample[0] = nn.Conv2d(1024, output_size, kernel_size=(1, 1), stride=(2, 2), bias=False)
                    self.model[7][0].downsample[1] = nn.BatchNorm2d(output_size, eps=1e-05, momentum=0.1, affine=True, track_running_stats=True)
                elif truncated_blocks == 2:
                    self.model[6][5].conv3 = nn.Conv2d(256, output_size, kernel_size=(1, 1), stride=(1, 1), bias=False)
                    self.model[6][5].bn3 = nn.BatchNorm2d(output_size, eps=1e-05, momentum=0.1, affine=True, track_running_stats=True)
                    self.model[6][0].downsample[0] = nn.Conv2d(512, output_size, kernel_size=(1, 1), stride=(2, 2), bias=False)
                    self.model[6][0].downsample[1] = nn.BatchNorm2d(output_size, eps=1e-05, momentum=0.1, affine=True, track_running_stats=True)
                elif truncated_blocks == 3:
                    self.model[5][3].conv3 = nn.Conv2d(128, output_size, kernel_size=(1, 1), stride=(1, 1), bias=False)
                    self.model[5][3].bn3 = nn.BatchNorm2d(output_size, eps=1e-05, momentum=0.1, affine=True, track_running_stats=True)
                    self.model[5][0].downsample[0] = nn.Conv2d(256, output_size, kernel_size=(1, 1), stride=(2, 2), bias=False)
                    self.model[5][0].downsample[1] = nn.BatchNorm2d(output_size, eps=1e-05, momentum=0.1, affine=True, track_running_stats=True)
                else:
                    print("Oops!  You truncate too much.  Try again...")

        elif model_type == 'res101':
            model = models.resnet101(pretrained=True)
            self.model = nn.Sequential(
                *list(model.children())[: -truncated_blocks-1]
            )
        
        # Fix forward parameters
        for param in self.model.parameters():
            param.requires_grad = False
        if finetune_feature_extraction:
            # Unlock conv4_3
            for param in list(self.model.parameters())[-finetune_layers :]:
                param.requires_grad = True
            
            if model_type == 'res50':
                if truncated_blocks == 1:
                    for param in list(self.model[7][0].downsample.parameters()):
                        param.requires_grad = True
                if truncated_blocks == 2:
                    for param in list(self.model[6][0].downsample.parameters()):
                        param.requires_grad = True
                if truncated_blocks == 3:
                    for param in list(self.model[5][0].downsample.parameters()):
                        param.requires_grad = True   
        if use_cuda:
            self.model = self.model.cuda()

    def forward(self, batch):
        output = self.model(batch)
        print(output.size())
        return output


class SoftDetectionModule(nn.Module):
    def __init__(self, soft_local_max_size=3):
        super(SoftDetectionModule, self).__init__()

        self.soft_local_max_size = soft_local_max_size

        self.pad = self.soft_local_max_size // 2

    def forward(self, batch):
        b = batch.size(0)

        batch = F.relu(batch)

        max_per_sample = torch.max(batch.view(b, -1), dim=1)[0]
        exp = torch.exp(batch / max_per_sample.view(b, 1, 1, 1))
        sum_exp = (
            self.soft_local_max_size ** 2 *
            F.avg_pool2d(
                F.pad(exp, [self.pad] * 4, mode='constant', value=1.),
                self.soft_local_max_size, stride=1
            )
        )
        local_max_score = exp / sum_exp

        depth_wise_max = torch.max(batch, dim=1)[0]
        depth_wise_max_score = batch / depth_wise_max.unsqueeze(1)

        all_scores = local_max_score * depth_wise_max_score
        score = torch.max(all_scores, dim=1)[0]

        score = score / torch.sum(score.view(b, -1), dim=1).view(b, 1, 1)

        return score


class D2Net(nn.Module):
    def __init__(self, model_file=None, use_cuda=True, finetune_layers=2, truncated_blocks=2, model_type=None, output_size=0):
        super(D2Net, self).__init__()

        self.dense_feature_extraction = DenseFeatureExtractionModule(
            finetune_feature_extraction=True,
            use_cuda=use_cuda,
            finetune_layers=finetune_layers,
            truncated_blocks=truncated_blocks,
            model_type=model_type,
            output_size=output_size
        )

        self.detection = SoftDetectionModule()

        if model_file is not None:
            if use_cuda:
                self.load_state_dict(torch.load(model_file)['model'])
            else:
                self.load_state_dict(torch.load(model_file, map_location='cpu')['model'])

    def forward(self, batch):
        b = batch['image1'].size(0)

        dense_features = self.dense_feature_extraction(
            torch.cat([batch['image1'], batch['image2']], dim=0)
        )

        scores = self.detection(dense_features)

        dense_features1 = dense_features[: b, :, :, :]
        dense_features2 = dense_features[b :, :, :, :]

        scores1 = scores[: b, :, :]
        scores2 = scores[b :, :, :]

        return {
            'dense_features1': dense_features1,
            'scores1': scores1,
            'dense_features2': dense_features2,
            'scores2': scores2
        }
