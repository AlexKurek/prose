from tqdm import tqdm
from astropy.io import fits
from prose.console_utils import TQDM_BAR_FORMAT
from astropy.wcs import WCS
import prose.visualisation as viz


class Unit:
    # TODO: add index self.i in image within unit loop
    # TODO: add dict of blocks and name on block to retrieve easily

    def __init__(self, blocks, name, fits_manager, files="light", show_progress=False, n_images=None, **kwargs):
        self.name = name
        self.blocks = blocks
        self.fits_manager = fits_manager

        self.retrieve_files(files, n_images=n_images)

        if show_progress:
            self.progress = lambda x: tqdm(
            x,
            desc=self.name,
            unit="files",
            ncols=80,
            bar_format=TQDM_BAR_FORMAT,
        )

        else:
            self.progress = lambda x: x

        self.data = {}

        if self.fits_manager.has_stack():
            self.stack_image = Image(self.fits_manager.get("stack")[0])

    def retrieve_files(self, keyword, n_images=None):
        self.fits_manager.files = self.fits_manager.get(keyword, n_images=n_images)
        self.files = self.fits_manager.files

    def get_data_header(self, file_path):
        return fits.getdata(file_path), fits.getheader(file_path)

    def run(self):
        if isinstance(self.files, list):
            if len(self.files) == 0:
                raise ValueError("No files to process")
        elif self.files is None:
            raise ValueError("No files to process")

        for block in self.blocks:
            block.initialize(self.fits_manager)

        stack_blocks = [block for block in self.blocks if block.stack]
        blocks = [block for block in self.blocks if not block.stack]
        has_stack_block = len(stack_blocks) > 0

        for block in stack_blocks:
            block.run(self.stack_image)
            block.stack_method(self.stack_image)

        for file_path in self.progress(self.files):
            image = Image(file_path)
            for block in blocks:
                if has_stack_block:
                    image.get_other_data(self.stack_image)
                block.run(image)

        for block in self.blocks:
            block.terminate()


class Image:

    def __init__(self, file_path, **kwargs):
        self.data = fits.getdata(file_path)
        self.header = fits.getheader(file_path)
        self.wcs = WCS(self.header)
        self.path = file_path
        self.__dict__.update(kwargs)

    def get_other_data(self, image):
        for key, value in image.__dict__.items():
            if key not in self.__dict__:
                self.__dict__[key] = value


class Block:

    def __init__(self, stack=False):
        self.stack = stack

    def initialize(self, *args):
        pass

    def run(self, image, **kwargs):
        raise NotImplementedError()

    def terminate(self):
        pass

    def stack_method(self, image):
        pass

    def show_image(self, image):
        viz.show_stars(image)


class PrintDim(Block):

    def __init__(self):
        pass

    def initialize(self, *args):
        print("I am a block")

    def run(self, image):
        pass


class Reduction(Unit):

    def __init__(self, ):
        pass

