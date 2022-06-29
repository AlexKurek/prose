from tqdm import tqdm
import xarray
from .console_utils import TQDM_BAR_FORMAT, warning, error
from collections import OrderedDict
from tabulate import tabulate
import numpy as np
from time import time
from .image import Image
from pathlib import Path
from functools import partial
import multiprocessing as mp
from .blocks.utils import DataBlock
import sys

class Sequence:
    # TODO: add index self.i in image within unit loop

    def __init__(self, blocks, name="", loader=Image):
        self.name = name
        self.files_or_images = []
        self.blocks = blocks
        self.loader = loader

        self.data = {}
        self.n_processed_images = None

    def __getattr__(self, item):
        return self.blocks_dict[item]

    @property
    def blocks(self):
        return list(self.blocks_dict.values())

    @blocks.setter
    def blocks(self, blocks):
        self.blocks_dict = OrderedDict({
            block.name if block.name is not None else "block{}".format(i): block
            for i, block in enumerate(blocks)
        })

    def run(self, images, show_progress=True, live_discard=False, telescope=None, terminate=True):
        discards = {}
        self.files_or_images = images if not isinstance(images, (str, Path, Image)) else [images]

        if show_progress:
            progress = lambda x: tqdm(
                x,
                desc=self.name,
                unit="images",
                ncols=80,
                bar_format=TQDM_BAR_FORMAT,
            )

        else:
            progress = lambda x: x

        if isinstance(self.files_or_images, list):
            if len(self.files_or_images) == 0:
                raise ValueError("No images to process")
        elif self.files_or_images is None:
            raise ValueError("No images to process")

        self.n_processed_images = 0

        # run
        for i, file_or_image in enumerate(progress(self.files_or_images)):
            if isinstance(file_or_image, (str, Path)):
                image = self.loader(file_or_image, telescope=telescope)
            else:
                image = file_or_image
            image.i = i
            self._last_image = image
            discard_message = False

            last_block = None

            for b, block in enumerate(self.blocks):
                # This allows to discard image in any Block
                if not image.discard:
                    block._run(image)
                    # except:
                    #     # TODO
                    #     if not last_block is None:
                    #         print(f"{type(last_block).__name__} failed")
                elif not discard_message:
                    last_block = type(self.blocks[b-1]).__name__
                    discard_message = True
                    if live_discard:
                        warning(f"image {i} discarded in {last_block}")
                    else:
                        if last_block not in discards:
                            discards[last_block] = []
                        discards[last_block].append(str(i))

            del image
            self.n_processed_images += 1


        if terminate:
            self.terminate()
        
        if not live_discard:
            for block_name, discarded in discards.items():
                warning(f"{block_name} discarded image{'s' if len(discarded)>1 else ''} {', '.join(discarded)}")

    def terminate(self):
        for block in self.blocks:
            block.terminate()

    def __str__(self):
        rows = [[
            i, block.name, block.__class__.__name__, f"{block.processing_time:.3f} s ({(block.processing_time/self.processing_time)*100:.0f}%)"] 
            for i, block in enumerate(self.blocks)
            ]
        headers = ["index", "name", "type", "processing"]

        return tabulate(rows, headers, tablefmt="fancy_grid")

    def __repr__(self) -> str:
        return self.__str__()

    def citations(self):
        citations = [block.citations() for block in self.blocks if block.citations() is not None]
        return citations if len(citations) > 0 else None

    def insert_before(self, before, block):
        pass

    @property
    def processing_time(self):
        return np.sum([block.processing_time for block in self.blocks])

    def __getitem__(self, item):
        return self.blocks[item]

    # import/export properties
    # ------------------------

    @staticmethod
    def from_dicts(blocks_dicts):
        blocks = []
        for block_dict in blocks_dicts:
            block = block_dict["block"](*block_dict["args"], **block_dict["kwargs"])
            block.name = block_dict["name"]
            blocks.append(block)
            
        return Sequence(blocks)

    @property
    def as_dicts(self):
        blocks = []
        for block in self.blocks:
            blocks.append(dict(
                block=block.__class__,
                name=block.name,
                args=block.args,
                kwargs=block.kwargs
            ))

        return blocks


class MPSequence(Sequence):

    def __init__(self, blocks, data_blocks=None, name="", loader=Image):
        super().__init__(blocks, name=name, loader=loader)
        if data_blocks is None:
            self.data = None
            self._has_data = False
        else:
            self.data = Sequence(data_blocks)
            self._has_data = True

    def check_data_blocks(self):
        bad_blocks = []
        for i, b in enumerate(self.blocks): 
            if isinstance(b, DataBlock):
                bad_blocks.append(f"{b.__class__.__name__}")
        if len(bad_blocks) > 0:
            bad_blocks = ', '.join(list(np.unique(bad_blocks)))
            error(f"Data blocks [{bad_blocks}] cannot be used in MPSequence\n\nConsider using the data_blocks kwargs")
            sys.exit()
    
    def run(self, images, globals=None, show_progress=True, live_discard=False, terminate=True):
        self.check_data_blocks()

        if globals is None:
            globals = {}
        self.files_or_images = images if not isinstance(images, (str, Path, Image)) else [images]

        if show_progress:
            def progress(x, total=None):
                return tqdm(
                x,
                desc=self.name,
                unit="images",
                ncols=80,
                bar_format=TQDM_BAR_FORMAT,
                total=total
            )

        else:
            progress = lambda x: x

        if isinstance(self.files_or_images, list):
            if len(self.files_or_images) == 0:
                raise ValueError("No images to process")
        elif self.files_or_images is None:
            raise ValueError("No images to process")

        self.n_processed_images = 0
        
        # run
        n = len(self.files_or_images)
        processed_blocks = mp.Manager().list(self.blocks)

        with mp.Pool() as pool:
            for image in progress(pool.imap(partial(
                _run_all,
                blocks=processed_blocks,
                loader=self.loader
            ), self.files_or_images), total=n):
                if self._has_data:
                    self.data.run(image, terminate=False, show_progress=False)
    
        # terminate
        if terminate and self._has_data:
                self.data.terminate()

def _run_all(file_or_image, blocks=None, loader=None):

    if isinstance(file_or_image, (str, Path)):
        image = loader(file_or_image)
    else:
        image = file_or_image

    discard_message = False
    last_block = None

    for b, block in enumerate(blocks):
        # This allows to discard image in any Block
        if not image.discard:
            block._run(image)
        elif not discard_message:
            last_block = blocks[b-1]
            discard_message = True
            warning(f"image ? discarded in {type(last_block).__name__}")

    return image