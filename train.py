import argparse
import os
import torch
import math
from torch.autograd import Variable
from torch.optim.lr_scheduler import ReduceLROnPlateau
from tqdm import tqdm

import models
import data
from nag import NAG
from average_meter import AverageMeter


parser = argparse.ArgumentParser(description='Convolutional Sequence to Sequence Training')
parser.add_argument('data', metavar='DIR',
                    help='path to data directory')
parser.add_argument('--arch', '-a', default='fconv_iwslt_de_en', metavar='ARCH',
                    choices=models.__all__,
                    help='model architecture ({})'.format(', '.join(models.__all__)))
parser.add_argument('-j', '--workers', default=16, type=int, metavar='N',
                    help='number of data loading workers (default: 16)')
parser.add_argument('--batch-size', '-b', default=32, type=int, metavar='N',
                    help='batch size')
parser.add_argument('--lr', '--learning-rate', default=0.25, type=float, metavar='LR',
                    help='initial learning rate')
parser.add_argument('--min-lr', metavar='LR', default=1e-5, type=float,
                    help='minimum learning rate')
parser.add_argument('--momentum', default=0.99, type=float, metavar='M',
                    help='momentum factor')
parser.add_argument('--clip-norm', default=25, type=float, metavar='NORM',
                    help='clip threshold of gradients')
parser.add_argument('--weight-decay', '--wd', default=0.0, type=float, metavar='WD',
                    help='weight decay')
parser.add_argument('--dropout', default=0.2, type=float, metavar='D',
                    help='dropout probability')
parser.add_argument('--embed-dim', '-d', metavar='DIM', default=256, type=int,
                    help='embedding dimension')
parser.add_argument('--save-dir', metavar='DIR', default='.',
                    help='path to save checkpoints')


def main():
    global args
    args = parser.parse_args()
    print(args)

    dataset = data.load(args.data)
    model = models.__dict__[args.arch](args, dataset)

    if torch.cuda.is_available():
        model.cuda()

    optimizer = NAG(model.parameters(), args.lr, momentum=args.momentum,
                    weight_decay=args.weight_decay)

    lr_scheduler = ReduceLROnPlateau(optimizer, patience=0)

    # Load the latest checkpoint if one is available
    epoch = load_checkpoint(model, optimizer, lr_scheduler)

    while optimizer.param_groups[0]['lr'] > args.min_lr:
        # train for one epoch
        train(epoch, model, dataset, optimizer)

        # evaluate on validate set
        val_loss = validate(epoch, model, dataset)

        # update the learning rate
        lr_scheduler.step(val_loss, epoch)
        epoch += 1

        # save checkpoint
        save_checkpoint(epoch, model, optimizer, lr_scheduler)


def train(epoch, model, dataset, optimizer):
    model.train()
    itr = dataset.dataloader('train', epoch=epoch, batch_size=args.batch_size)
    loss_meter = AverageMeter()

    def step(sample):
        sample = prepare_sample(sample)

        loss = model(**sample)

        optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm(model.parameters(), args.clip_norm)
        optimizer.step()

        return loss.data[0] / math.log(2)

    t = tqdm(itr, leave=False)
    t.set_description('epoch {}'.format(epoch))
    for sample in t:
        loss = step(sample)
        loss_meter.update(loss, sample['ntokens'])
        t.set_postfix(loss='{:.2f} ({:.2f})'.format(loss, loss_meter.avg),
                      lr=optimizer.param_groups[0]['lr'])

    t.write('| epoch {:03d} | train loss {:2.2f} | train ppl {:2.2f} | lr {:0.6f}'
            .format(epoch, loss_meter.avg, math.pow(2, loss_meter.avg),
                    optimizer.param_groups[0]['lr']))


def validate(epoch, model, dataset):
    model.eval()
    itr = dataset.dataloader('valid', epoch=epoch, batch_size=args.batch_size)
    loss_meter = AverageMeter()

    def step(sample):
        sample = prepare_sample(sample, volatile=True)
        loss = model(**sample)
        return loss.data[0] / math.log(2)

    t = tqdm(itr, leave=False)
    t.set_description('val {}'.format(epoch))
    for sample in t:
        loss = step(sample)
        loss_meter.update(loss, sample['ntokens'])
        t.set_postfix(loss='{:.2f}'.format(loss_meter.avg))

    t.write('| epoch {:03d} | val loss {:2.2f} | val ppl {:2.2f}'
            .format(epoch, loss_meter.avg, math.pow(2, loss_meter.avg)))

    return loss_meter.avg


def save_checkpoint(epoch, model, optimizer, lr_scheduler):
    state_dict = {
        'epoch': epoch,
        'model': model.state_dict(),
        'optimizer': optimizer.state_dict(),
        'best_loss': lr_scheduler.best,
    }
    filename = os.path.join(args.save_dir, 'checkpoint.pt')
    torch.save(state_dict, filename)


def load_checkpoint(model, optimizer, lr_scheduler):
    filename = os.path.join(args.save_dir, 'checkpoint.pt')
    if not os.path.exists(filename):
        return 0

    state = torch.load(filename)
    model.load_state_dict(state['model'])
    optimizer.load_state_dict(state['optimizer'])
    lr_scheduler.best = state['best_loss']
    epoch = state['epoch']

    print(' | loaded checkpoint {} (epoch {})'.format(filename, epoch))
    return epoch


def prepare_sample(sample, volatile=False):
    r = {}
    for key in ['input_tokens', 'input_positions', 'target', 'src_tokens', 'src_positions']:
        tensor = sample[key]
        if torch.cuda.is_available():
            tensor = tensor.cuda()
        r[key] = Variable(tensor, volatile=volatile)
    return r


if __name__ == '__main__':
    main()
