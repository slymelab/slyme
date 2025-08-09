"""
Metaclasses used in slyme.

NOTE: It is recommended to inherit classes in the ``metabase`` rather than directly 
specifying the metaclass using metaclasses defined here, because some metaclasses 
should be used together with a plain super class, and 
directly specifying them as the metaclass won't work.

NOTE: We name all the metaclasses with ``Metaclass`` rather than the abbreviation 
``Meta``, because there already exists the ``Meta`` feature (although it has been 
deprecated), and we want to distinguish between these two concepts.
"""
