# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
#
#   See COPYING file distributed along with the PyMVPA package for the
#   copyright and license terms.
#
### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
"""PyMVPA's common Dataset container."""

__docformat__ = 'restructuredtext'

import numpy as np
import copy

from mvpa2.base import warning
from mvpa2.base.collections import SampleAttributesCollection, \
        FeatureAttributesCollection, DatasetAttributesCollection, \
        SampleAttribute, FeatureAttribute, DatasetAttribute
from mvpa2.base.dataset import AttrDataset
from mvpa2.base.dataset import _expand_attribute
from mvpa2.misc.support import idhash as idhash_
from mvpa2.mappers.base import ChainMapper
from mvpa2.featsel.base import StaticFeatureSelection
from mvpa2.mappers.flatten import mask_mapper, FlattenMapper

if __debug__:
    from mvpa2.base import debug


class Dataset(AttrDataset):
    __doc__ = AttrDataset.__doc__

    def get_mapped(self, mapper):
        """Feed this dataset through a trained mapper (forward).

        Parameters
        ----------
        mapper : Mapper
          This mapper instance has to be trained.

        Returns
        -------
        Dataset
          The forward-mapped dataset.
        """
        # if we use .forward, no postcall is called... is that
        #  desired?  doesn't seem to have major impact on unittests
        #  BUT since postcall might change dimensionality/meaning of
        #  data, it would not be any longer reversible; more over
        #  since chain of .forwards do not invoke postcalls, also
        #  forward would lead to different behavior
        #mds = mapper(self)
        mds = mapper.forward(self)
        mds._append_mapper(mapper)
        return mds


    def _append_mapper(self, mapper):
        if not 'mapper' in self.a:
            self.a['mapper'] = mapper
            return

        pmapper = self.a.mapper
        # otherwise we have a mapper already, but is it a chain?
        if not isinstance(pmapper, ChainMapper):
            self.a.mapper = ChainMapper([pmapper])

        # is a chain mapper
        # merge slicer?
        lastmapper = self.a.mapper[-1]
        if isinstance(lastmapper, StaticFeatureSelection):
            try:
                # try whether mappers can be merged
                lastmapper += mapper
            except TypeError:
                # append new one if not
                self.a.mapper.append(mapper)
        else:
            self.a.mapper.append(mapper)


    def __getitem__(self, args):
        # uniformize for checks below; it is not a tuple if just single slicing
        # spec is passed
        if not isinstance(args, tuple):
            args = (args,)

        # if we get an slicing array for feature selection and it is *not* 1D
        # try feeding it through the mapper (if there is any)
        if len(args) > 1 and isinstance(args[1], np.ndarray) \
           and len(args[1].shape) > 1 \
           and self.a.has_key('mapper'):
            args = list(args)
            args[1] = self.a.mapper.forward1(args[1])
            args = tuple(args)

        # let the base do the work
        ds = super(Dataset, self).__getitem__(args)

        # and adjusting the mapper (if any)
        if len(args) > 1 and 'mapper' in ds.a:
            # create matching mapper
            # the mapper is just appended to the dataset. It could also be
            # actually used to perform the slicing and prevent duplication of
            # functionality between the Dataset.__getitem__ and the mapper.
            # However, __getitem__ is sometimes more efficient, since it can
            # slice samples and feature axis at the same time. Moreover, the
            # mvpa2.base.dataset.Dataset has no clue about mappers and should
            # be fully functional without them.
            subsetmapper = StaticFeatureSelection(args[1],
                                              dshape=self.samples.shape[1:])
            # do not-act forward mapping to charge the output shape of the
            # slice mapper without having it to train on a full dataset (which
            # is most likely more expensive)
            subsetmapper.forward(np.zeros((1,) + self.shape[1:], dtype='bool'))
            # mapper is ready to use -- simply store
            ds._append_mapper(subsetmapper)

        return ds


    def find_collection(self, attr):
        """Lookup collection that contains an attribute of a given name.

        Collections are search in the following order: sample attributes,
        feature attributes, dataset attributes. The first collection
        containing a matching attribute is returned.

        Parameters
        ----------
        attr : str
          Attribute name to be looked up.

        Returns
        -------
        Collection
          If not matching collection is found a LookupError exception is raised.
        """
        if attr in self.sa:
            col = self.sa
            if __debug__ and (attr in self.fa or attr in self.a):
                warning("An attribute with name '%s' is also present "
                        "in another attribute collection (fa=%s, a=%s) -- make "
                        "sure that you got the right one (see ``col`` "
                        "argument)." % (attr, attr in self.fa, attr in self.a))
        elif attr in self.fa:
            col = self.fa
            if __debug__ and attr in self.a:
                warning("An attribute with name '%s' is also present "
                        "in the dataset attribute collection -- make sure "
                        "that you got the right one (see ``col`` argument)."
                        % (attr,))
        elif attr in self.a:
            col = self.a
            # we don't need to warn here, since it wouldn't happen
        else:
            raise LookupError("Cannot find '%s' attribute in any dataset "
                              "collection." % attr)
        return col


    def _collection_id2obj(self, col):
        if col == 'sa':
            col = self.sa
        elif col == 'fa':
            col = self.fa
        elif col == 'a':
            col = self.a
        else:
            raise LookupError("Unknown collection '%s'. Possible values "
                              "are: 'sa', 'fa', 'a'." % col)
        return col


    def set_attr(self, name, value):
        """Set an attribute in a collection.

        Parameters
        ----------
        name : str
          Collection and attribute name. This has to be in the same format as
          for ``get_attr()``.
        value : array
          Value of the attribute.
        """
        if '.' in name:
            col, name = name.split('.')[0:2]
            # translate collection names into collection
            col = self._collection_id2obj(col)
        else:
            # auto-detect collection
            col = self.find_collection(name)

        col[name] = value


    def get_attr(self, name):
        """Return an attribute from a collection.

        A collection can be specified, but can also be auto-detected.

        Parameters
        ----------
        name : str
          Attribute name. The attribute name can also be prefixed with any valid
          collection name ('sa', 'fa', or 'a') separated with a '.', e.g.
          'sa.targets'. If no collection prefix is found auto-detection of the
          collection is attempted.

        Returns
        -------
        (attr, collection)
          2-tuple: First element is the requested attribute and the second
          element is the collection that contains the attribute. If no matching
          attribute can be found a LookupError exception is raised.
        """
        if '.' in name:
            col, name = name.split('.')[0:2]
            # translate collection names into collection
            col = self._collection_id2obj(col)
        else:
            # auto-detect collection
            col = self.find_collection(name)

        return (col[name], col)


    def item(self):
        """Provide the first element of samples array.

        Notes
        -----
        Introduced to provide compatibility with `numpy.asscalar`.
        See `numpy.ndarray.item` for more information.
        """
        return self.samples.item()


    @property
    def idhash(self):
        """To verify if dataset is in the same state as when smth else was done

        Like if classifier was trained on the same dataset as in question
        """

        res = 'self@%s samples@%s' % (idhash_(self), idhash_(self.samples))

        for col in (self.a, self.sa, self.fa):
            # We cannot count on the order the values in the dict will show up
            # with `self._data.value()` and since idhash will be order-dependent
            # we have to make it deterministic
            keys = col.keys()
            keys.sort()
            for k in keys:
                res += ' %s@%s' % (k, idhash_(col[k].value))
        return res


    @classmethod
    def from_wizard(cls, samples, targets=None, chunks=None, mask=None,
                    mapper=None, flatten=None, space=None):
        """Convenience method to create dataset.

        Datasets can be created from N-dimensional samples. Data arrays with
        more than two dimensions are going to be flattened, while preserving
        the first axis (separating the samples) and concatenating all other as
        the second axis. Optionally, it is possible to specify targets and
        chunk attributes for all samples, and masking of the input data (only
        selecting elements corresponding to non-zero mask elements

        Parameters
        ----------
        samples : ndarray
          N-dimensional samples array. The first axis separates individual
          samples.
        targets : scalar or ndarray, optional
          Labels for all samples. If a scalar is provided its values is assigned
          as label to all samples.
        chunks : scalar or ndarray, optional
          Chunks definition for all samples. If a scalar is provided its values
          is assigned as chunk of all samples.
        mask : ndarray, optional
          The shape of the array has to correspond to the shape of a single
          sample (shape(samples)[1:] == shape(mask)). Its non-zero elements
          are used to mask the input data.
        mapper : Mapper instance, optional
          A trained mapper instance that is used to forward-map
          possibly already flattened (see flatten) and masked samples
          upon construction of the dataset. The mapper must have a
          simple feature space (samples x features) as output. Use a
          `ChainMapper` to achieve that, if necessary.
        flatten : None or bool, optional
          If None (default) and no mapper provided, data would get flattened.
          Bool value would instruct explicitly either to flatten before
          possibly passing into the mapper if no mask is given.
        space : str, optional
          If provided it is assigned to the mapper instance that performs the
          initial flattening of the data.

        Returns
        -------
        instance : Dataset
        """
        # for all non-ndarray samples you need to go with the constructor
        samples = np.asanyarray(samples)

        # compile the necessary samples attributes collection
        sa_items = {}

        if not targets is None:
            sa_items['targets'] = _expand_attribute(targets,
                                                   samples.shape[0],
                                                  'targets')

        if not chunks is None:
            # unlike previous implementation, we do not do magic to do chunks
            # if there are none, there are none
            sa_items['chunks'] = _expand_attribute(chunks,
                                                   samples.shape[0],
                                                   'chunks')

        # common checks should go into __init__
        ds = cls(samples, sa=sa_items)
        # apply mask through mapper
        if mask is None:
            # if we have multi-dim data
            if len(samples.shape) > 2 and \
                   ((flatten is None and mapper is None) # auto case
                    or flatten):                         # bool case
                fm = FlattenMapper(shape=samples.shape[1:], space=space)
                ds = ds.get_mapped(fm)
        else:
            mm = mask_mapper(mask, space=space)
            mm.train(ds)
            ds = ds.get_mapped(mm)

        # apply generic mapper
        if not mapper is None:
            ds = ds.get_mapped(mapper)
        return ds


    @classmethod
    def from_channeltimeseries(cls, samples, targets=None, chunks=None,
                               t0=None, dt=None, channelids=None):
        """Create a dataset from segmented, per-channel timeseries.

        Channels are assumes to contain multiple, equally spaced acquisition
        timepoints. The dataset will contain additional feature attributes
        associating each feature with a specific `channel` and `timepoint`.

        Parameters
        ----------
        samples : ndarray
          Three-dimensional array: (samples x channels x timepoints).
        t0 : float
          Reference time of the first timepoint. Can be used to preserve
          information about the onset of some stimulation. Preferably in
          seconds.
        dt : float
          Temporal distance between two timepoints. Preferably in seconds.
        channelids : list
          List of channel names.
        targets, chunks
          See `Dataset.from_wizard` for documentation about these arguments.
        """
        # check samples
        if len(samples.shape) != 3:
            raise ValueError(
                "Input data should be (samples x channels x timepoints. Got: %s"
                % samples.shape)

        if not t0 is None and not dt is None:
            timepoints = np.arange(t0, t0 + samples.shape[2] * dt, dt)
            # broadcast over all channels
            timepoints = np.vstack([timepoints] * samples.shape[1])
        else:
            timepoints = None

        if not channelids is None:
            if len(channelids) != samples.shape[1]:
                raise ValueError(
                    "Number of channel ids does not match channels in the "
                    "sample data. Expected %i, but got %i"
                    % (samples.shape[1], len(channelids)))
            # broadcast over all timepoints
            channelids = np.dstack([channelids] * samples.shape[2])[0]

        ds = cls.from_wizard(samples, targets=targets, chunks=chunks)

        # add additional attributes
        if not timepoints is None:
            ds.fa['timepoints'] = ds.a.mapper.forward1(timepoints)
        if not channelids is None:
            ds.fa['channels'] = ds.a.mapper.forward1(channelids)

        return ds


    # shortcut properties
    S = property(fget=lambda self:self.samples)
    targets = property(fget=lambda self:self.sa.targets,
                      fset=lambda self, v:self.sa.__setattr__('targets', v))
    uniquetargets = property(fget=lambda self:self.sa['targets'].unique)

    T = targets
    UT = property(fget=lambda self:self.sa['targets'].unique)
    chunks = property(fget=lambda self:self.sa.chunks,
                      fset=lambda self, v:self.sa.__setattr__('chunks', v))
    uniquechunks = property(fget=lambda self:self.sa['chunks'].unique)
    C = chunks
    UC = property(fget=lambda self:self.sa['chunks'].unique)
    mapper = property(fget=lambda self:self.a.mapper)
    O = property(fget=lambda self:self.a.mapper.reverse(self.samples))


# convenience alias
dataset_wizard = Dataset.from_wizard


class HollowSamples(object):
    """Samples container that doesn't store samples.

    The purpose of this class is to provide an object that can be used as
    ``samples`` in a Dataset, without having actual samples. Instead of storing
    multiple samples it only maintains a IDs for samples and features it
    pretends to contain.

    Using this class in a dataset in conjuction will actual attributes, will
    yield a lightweight dataset that is compatible with the majority of all
    mappers and can be used to 'simulate' processing by mappers. The class
    offers acces to the sample and feature IDs via its ``sid`` and ``fid``
    members.
    """
    def __init__(self, shape=None, sid=None, fid=None, dtype=np.float):
        """
        Parameters
        ----------
        shape : 2-tuple or None
          Shape of the pretend-sample array (nsamples x nfeatures). Can be
          left out if both ``sid`` and ``fid`` are provided.
        sid : 1d-array or None
          Vector of sample IDs. Can be left out if ``shape`` is provided.
        fid : 1d-array or None
          Vector of feature IDs. Can be left out if ``shape`` is provided.
        dtype : type or str
          Pretend-datatype of the non-existing samples.
        """
        if shape is None and sid is None and fid is None:
            raise ValueError("Either shape or ID vectors have to be given")
        if not shape is None and not len(shape) == 2:
            raise ValueError("Only two-dimensional shapes are supported")
        if sid is None:
            self.sid = np.arange(shape[0], dtype='uint')
        else:
            self.sid = sid
        if fid is None:
            self.fid = np.arange(shape[1], dtype='uint')
        else:
            self.fid = fid
        self.dtype = dtype
        # sanity check
        if not shape is None and not len(self.sid) == shape[0] \
                and not len(self.fid) == shape[1]:
            raise ValueError("Provided ID vectors do not match given `shape`")


    def __reduce__(self):
        return (self.__class__,
                ((len(self.sid), len(self.fid)),
                 self.sid,
                 self.fid,
                 self.dtype))

    def copy(self, deep=True):
        return deep and copy.deepcopy(self) or copy.copy(self)

    @property
    def shape(self):
        return (len(self.sid), len(self.fid))


    @property
    def samples(self):
        return np.zeros((len(self.sid), len(self.fid)), dtype=self.dtype)


    def __array__(self, dtype=None):
        # come up with a fake array of proper dtype
        return np.zeros((len(self.sid), len(self.fid)), dtype=self.dtype)


    def __getitem__(self, args):
        if not isinstance(args, tuple):
            args = (args,)

        if len(args) > 2:
            raise ValueError("Too many arguments (%i). At most there can be "
                             "two arguments, one for samples selection and one "
                             "for features selection" % len(args))

        if len(args) == 1:
            args = [args[0], slice(None)]
        else:
            args = [a for a in args]
        # ints need to become lists to prevent silent dimensionality changes
        # of the arrays when slicing
        for i, a in enumerate(args):
            if isinstance(a, int):
                args[i] = [a]
        # apply to vectors
        sid = self.sid[args[0]]
        fid = self.fid[args[1]]

        return HollowSamples((len(sid), len(fid)), sid=sid, fid=fid,
                             dtype=self.dtype)

    def view(self):
        """Return itself"""
        return self
