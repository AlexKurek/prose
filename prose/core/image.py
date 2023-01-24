import matplotlib.pyplot as plt
import numpy as np
from astropy.coordinates import SkyCoord
import astropy.units as u
from astropy.coordinates import Angle
from dateutil import parser as dparser
from astropy.wcs import WCS
from .. import viz, utils, Telescope
from astropy.io import fits
from datetime import timedelta
from pathlib import Path
from ..core.source import Sources
from matplotlib import gridspec
from pathlib import Path
from prose import Telescope
from prose.core.image import *
from astropy.nddata import Cutout2D as astopy_Cutout2D
from astropy.wcs.wcs import WCS
from astropy.io.fits.hdu.base import _BaseHDU
from PIL import Image
from copy import deepcopy, copy

class Image:
    def __init__(self, data=None, metadata=None, computed=None):
        self.data = data
        self.metadata = metadata if metadata is not None else {}

        self.catalogs = {}
        self._sources = Sources([])
        self.discard = False
        self.origin = (0, 0)
        self.computed = computed if computed is not None else {}

    def copy(self, data=True):
        """Copy of image object

        Parameters
        ----------
        data : bool, optional
            whether to copy data, by default True

        Returns
        -------
        Image
            copied object
        """
        new_self = Image(
            deepcopy(self.data) if data else None,
            deepcopy(self.metadata),
            deepcopy(self.computed),
        )

        del new_self.__dict__["catalogs"]
        del new_self.__dict__["_sources"]

        new_self.catalogs = deepcopy(self.catalogs)
        new_self._sources = copy(self._sources)

        return new_self

    def __copy__(self):
        return self.copy()

    def show(
        self,
        cmap="Greys_r",
        ax=None,
        figsize=8,
        zscale=True,
        frame=False,
        contrast=0.1,
        **kwargs,
    ):
        """Show image data

        Parameters
        ----------
        cmap : str, optional
            matplotlib colormap, by default "Greys_r"
        ax : subplot, optional
            matplotlbib Axes in which to plot, by default None
        figsize : tuple, optional
            matplotlib figure size if ax not sepcified, by default (10,10)
        stars : bool, optional
            whether to show ``Image.stars_coords``, by default None
        stars_labels : bool, optional
            whether top show stars indexes, by default True
        zscale : bool, optional
            whether to apply a z scale to plotted image data, by default False
        frame : bool, optional
            whether to show astronomical coordinates axes, by default False
        contrast : float, optional
            image contrast used in image scaling, by default 0.1
        ms: int
            stars markers size
        ft: int
            stars label font size

        See also
        --------
        show_cutout :
            Show a specific star cutout
        plot_catalog :
            Plot catalog stars on an image
        plot_circle :
            Plot circle with radius in astronomical units
        """
        if ax is None:
            if not isinstance(figsize, (list, tuple)):
                if isinstance(figsize, (float, int)):
                    figsize = (figsize, figsize)
                else:
                    raise TypeError("figsize must be tuple or list or float or int")
            fig = plt.figure(figsize=figsize)
            if frame:
                ax = fig.add_subplot(111, projection=self.wcs)
            else:
                ax = fig.add_subplot(111)

        if zscale is False:
            vmin = np.nanmedian(self.data)
            vmax = vmax = vmin * (1 + contrast) / (1 - contrast)
            _ = ax.imshow(
                self.data, cmap=cmap, origin="lower", vmin=vmin, vmax=vmax, **kwargs
            )
        else:
            _ = ax.imshow(
                utils.z_scale(self.data, contrast), cmap=cmap, origin="lower", **kwargs
            )

        if frame:
            overlay = ax.get_coords_overlay(self.wcs)
            overlay.grid(color="white", ls="dotted")
            overlay[0].set_axislabel("Right Ascension (J2000)")
            overlay[1].set_axislabel("Declination (J2000)")

    def _from_metadata_with_unit(self, name):
        unit_name = f"{name}_unit"
        value = self.metadata[name]
        unit = str_to_astropy_unit(self.metadata[unit_name])
        if name in ["ra", "dec"]:
            return Angle(value, unit).to(u.deg)
        else:
            return value * unit
            ""

    @property
    def shape(self):
        return np.array(self.data.shape)

    @property
    def ra(self):
        return self._from_metadata_with_unit("ra")

    @property
    def dec(self):
        return self._from_metadata_with_unit("dec")
    
    @property
    def exposure(self):
        return self._from_metadata_with_unit("exposure")
    
    @property
    def jd(self):
        return self.metadata["jd"]

    @property
    def pixel_scale(self):
        return self._from_metadata_with_unit("pixel_scale")

    @property
    def filter(self):
        return self.metadata["filter"]

    @property
    def fov(self):
        """RA-DEC field of view of the image in degrees

        Returns
        -------
        astropy.units Quantity
        """
        return np.array(self.shape)[::-1] * self.pixel_scale.to(u.deg)

    @property
    def date(self):
        """datetime of the observation

        Returns
        -------
        datetime.datetime
        """
        return dparser.parse(self.metadata["date"])

    @property
    def night_date(self):
        """date of the night when night started.

        Returns
        -------
        datetime.date
        """
        # TODO: do according to last astronomical twilight?
        return (self.date - timedelta(hours=15)).date()

    def set(self, name, value):
        self.computed[name] = value

    def get(self, name):
        return self.computed[name]

    @property
    def sources(self):
        """Image sources
        
        Returns
        -------
        prose.core.source.Sources
        """
        return self._sources

    @sources.setter
    def sources(self, new_sources):
        if isinstance(new_sources, Sources):
            self._sources = new_sources
        else:
            self._sources = Sources(np.array(new_sources))

    def show_cutout(self, star=None, size=200, **kwargs):
        """Show a zoomed cutout around a detected star or coordinates

        Parameters
        ----------
        star : [type], optional
            detected star id or (x, y) coordinate, by default None
        size : int, optional
            side size of square cutout in pixel, by default 200
        **kwargs passed to self.show
        """

        if star is None:
            x, y = self.stars_coords[self.target]
        elif isinstance(star, int):
            x, y = self.stars_coords[star]
        elif isinstance(star, (tuple, list, np.ndarray)):
            x, y = star
        else:
            raise ValueError("star type not understood")

        self.show(stars=False, **kwargs)
        plt.xlim(np.array([-size / 2, size / 2]) + x)
        plt.ylim(np.array([-size / 2, size / 2]) + y)
        self.plot_sources()

    def cutout(self, coords, shape, wcs=True):
        """Return a list of Image cutouts from the image

        Parameters
        ----------
        coords : np.ndarray
            (N, 2) array of cutouts center coordinates 
        shape : tuple
            The shape of the cutouts to extract
        wcs : bool, optional
            wether to compute and include cutouts WCS (takes more time), by default True

        Returns
        -------
        list of Image
            image cutouts
        """
        new_image = astopy_Cutout2D(
            self.data,
            coords,
            shape,
            wcs=self.wcs if wcs else None,
            fill_value=np.nan,
            mode="partial",
        )

        # get sources
        new_sources = []
        if len(self._sources) > 0:
            sources_in = np.all(
                np.abs(self.sources.coords - coords) < np.array(shape)[::-1] / 2, 1
            )
            sources = self._sources[sources_in]

            for s in sources:
                _s = s.copy()
                _s.coords = _s.coords - coords + np.array(shape)[::-1] / 2
                new_sources.append(_s)

        image = Image(new_image.data, deepcopy(self.metadata), deepcopy(self.computed))
        image._sources = Sources(new_sources)
        image.wcs = new_image.wcs

        return image


    @property
    def wcs(self):
        """astropy.wcs.WCS object associated with the FITS ``Image.header``"""
        return WCS(self.metadata.get("wcs", None))

    @wcs.setter
    def wcs(self, new_wcs):
        if new_wcs is not None:
            if isinstance(new_wcs, WCS):
                self.metadata["wcs"] = dict(new_wcs.to_header())

    @property
    def plate_solved(self):
        """Return whether the image is plate solved"""
        return self.wcs.has_celestial

    def writeto(self, destination):
        """TODO"""
        hdu = fits.PrimaryHDU(
            data=self.data, header=fits.Header(utils.clean_header(self.header))
        )
        hdu.writeto(destination, overwrite=True)

    @property
    def skycoord(self):
        """astropy SkyCoord object based on header RAn, DEC"""
        return SkyCoord(self.ra, self.dec, frame="icrs")

    def plot_catalog(self, name, color="y", label=False, n=100000):
        """Plot catalog stars

        must be over :py:class:`Image.show` or :py:class:`Image.show_cutout` plot

        Parameters
        ----------
        name : str
            catalog name as stored in :py:class:Image.catalog`
        color : str, optional
            color of stars markers, by default "y"
        label : bool, optional
            whether to show stars catalogs ids, by default False
        n : int, optional
            number of brightest catalog stars to show, by default 100000
        """
        assert (
            name in self.catalogs
        ), f"Catalog '{name}' not present, consider using ..."
        x, y = self.catalogs[name][["x", "y"]].values[0:n].T
        labels = self.catalogs[name]["id"].values if label else None
        viz.plot_marks(x, y, labels, color=color)

    def plot_model(self, data, figsize=(5, 5), cmap=None, c="C0", contour=False):

        plt.figure(figsize=figsize)
        axes = gridspec.GridSpec(2, 2, width_ratios=[9, 2], height_ratios=[2, 9])
        axes.update(wspace=0, hspace=0)

        # axtt = plt.subplot(gs[1, 1])
        ax = plt.subplot(axes[1, 0])
        axr = plt.subplot(axes[1, 1], sharey=ax)
        axt = plt.subplot(axes[0, 0], sharex=ax)

        ax.imshow(self.data, alpha=1, cmap=cmap, origin="lower")
        if contour:
            ax.contour(data, colors="w", alpha=0.7)

        x, y = np.indices(data.shape)

        axt.plot(y[0], np.mean(self.data, axis=0), c=c, label="data")
        axt.plot(y[0], np.mean(data, axis=0), "--", c="k", label="model")
        axt.axis("off")
        axt.legend()

        axr.plot(np.mean(self.data, axis=1), y[0], c=c)
        axr.plot(np.mean(data, axis=1), y[0], "--", c="k")
        axr.axis("off")
    
    def __setattr__(self, name, value):
        if hasattr(self, name):
            super().__setattr__(name, value)
        else:
            if "computed" in self.__dict__:
                self.computed[name] = value
            else:
                super().__setattr__(name, value)
            
    def __getattr__(self, name):
        if "computed" not in self.__dict__:
            super.__getattr__(self, name)
        else:
            if name in self.computed:
                return self.computed[name]
            else:
                raise AttributeError()

def str_to_astropy_unit(unit_string):
    return u.__dict__[unit_string]


def FITSImage(filepath_or_hdu, verbose=False, load_units=True, load_data=True, telescope=None):
    """Create an image from a FITS file

    Parameters
    ----------
    filepath : str
        path of fits file
    verbose : bool, optional
        wether to be verbose, by default False
    load_units : bool, optional
        wether to load metadata units, by default True
    load_data : bool, optional
        wether to load image data, by default True

    Returns
    -------
    _type_
        _description_

    Raises
    ------
    ValueError
        _description_
    """
    if isinstance(filepath_or_hdu, (str, Path)):
        values = fits.getdata(filepath_or_hdu).astype(float) if load_data else None
        header = fits.getheader(filepath_or_hdu)
        path = filepath_or_hdu
    elif issubclass(type(filepath_or_hdu), _BaseHDU):
        values = filepath_or_hdu.data
        header = filepath_or_hdu.header
        path = None
    else:
        raise ValueError("filepath must be a str")

    if telescope is None:
        telescope = Telescope.from_names(
            header.get("INSTRUME", ""), header.get("TELESCOP", ""), verbose=verbose
        )

    metadata = {
        "telescope": telescope.name,
        "exposure": header.get(telescope.keyword_exposure_time, None),
        "ra": header.get(telescope.keyword_ra, None),
        "dec": header.get(telescope.keyword_dec, None),
        "filter": header.get(telescope.keyword_filter, None),
        "date": header.get(telescope.keyword_observation_date, None),
        "jd": header.get(telescope.keyword_jd, None),
        "object": header.get(telescope.keyword_object, None),
        "pixel_scale": telescope.pixel_scale,
        "overscan": telescope.trimming[::-1],
        "path": path,
        "dimensions": (header.get("NAXIS1", 1), header.get("NAXIS2", 1)),
        "type": telescope.image_type(header)
    }

    if load_units:
        metadata.update(
            {
                "exposure_unit": "s",
                "ra_unit": telescope.ra_unit.name,
                "dec_unit": telescope.dec_unit.name,
                "jd_scale": telescope.jd_scale,
                "pixel_scale_unit": "arcsec",
            }
        )

    image = Image(values, metadata, {})
    image.fits_header = header
    image.wcs = WCS(header)
    image.telescope = telescope

    return image