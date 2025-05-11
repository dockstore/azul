.. sectnum::
    :suffix: .

Data mirroring
##############

This is a draft specification of the data mirroring facility in Azul. The
facility is currently under construction in an effort to make all public data in
the Human Cell Atlas [1]_ available in AWS S3, under the auspices of the Open
Data Sponsorship Program [2]_. This specification may not be fully implemented
at this time and is subject to change as the implementation progresses.

.. [1] https://www.humancellatlas.org/
.. [2] https://aws.amazon.com/opendata/open-data-sponsorship-program/


Mirror bucket layout
====================

A mirror bucket is an AWS S3 bucket. The bucket layout employs content-based
addressing in order to allow for efficient mirroring and to avoid redundantly
storing duplicate files. The bucket contains three types of objects: file
objects, alias objects and info objects.


File objects
++++++++++++

A file object holds a file's content, a sequence of bytes. There is one file
object per unique sequence of bytes. If two files have the same content, there
is only one file object in the mirror, representing both. The key of a file
object is ``file/${digest_value}.${digest_type}`` where ``digest_value`` is the
hexadecimal form of a hash of the file object's content and ``digest_type`` is
one of ``sha1``, ``md5`` or ``sha256``, denoting the type of algorithm used to
derive that hash. Henceforth we'll be referring to the pair of ``digest_type``
and ``digest_value`` as *digest*.


Alias objects
+++++++++++++

Alias objects are used to make a file object accessible under hash algorithms
other than the algorithm specified in the file object's key. The key of an alias
object is ``alias/${digest_value}.${digest_type}.json`` where ``digest_value``
is the hexadecimal form of a hash of a file object's content and ``digest_type``
is one of ``sha1``, ``md5`` or ``sha256``, denoting the type of algorithm used
to derive that hash. The content of an alias object is JSON of the form
``{"schema":"…", "digest_value":…, "digest_type":…}`` where ``digest_value`` and
``digest_type`` represent the digest to be used when composing the aliased file
object's key.

The ``schema`` property facilitates future changes to the format of aliase
objects. For details see the `Schemas`_ section below.


Info objects
++++++++++++

Info objects contain JSON further describing a file. The key of an info object
is ``info/${digest_value}.${digest_type}.json`` where ``digest_value`` is the
hexadecimal form of a hash of the corresponding file object's content and
``digest_type`` is one of ``sha1``, ``md5`` or ``sha256``, denoting the type of
algorithm used to derive that hash. The content of an ``info`` object is JSON of
the form ``{"schema":"…", "content-type":…}``.

The ``content-type`` property contains the content type of the file, as defined
for the HTTP response header of the same name [4]_.

.. [4] https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Type

The ``schema`` property facilitates future changes to the format of info
objects. For details see the `Schemas`_ section below.


Schemas
=======

The ``schema`` property of alias and info objects contains, and always will
contain, the URL of a JSON schema [3]_ that the alias and info objects' JSON
content conforms to. The last path component of the schema URL is, and will
always be, ``v${schema_version}.json`` where ``schema_version`` is a
monotonically increasing integer.

The contents of a schema at a given URL may change without a change to the URL,
but only in backwards compatible ways, i.e. by adding a new property. Backwards
incompatible schema changes will lead to an increment in the version.
Programmatic consumers of alias and info objects should check the version number
encoded in the schema URL stored in the object prior to consuming the rest of
the object and should not attempt to consume the remainder of an object with an
unexpected schema version.

Other parts of a schema URL may change without notice. Consumers of alias and
info objects should not make any assumptions about those parts. Consumers may
only assume that a request to the URL yields a valid JSON schema, that the last
path component encodes the schema version and that different schema versions are
incompatible to each other.

.. [3] https://json-schema.org/


Constraints and invariants
==========================

The digest stored in an alias object is always different to the digest encoded
in its key. In other words, there are no redundant aliases.

If there is an alias object, the aliased file object is guaranteed to exist.

If there is an info object for a given digest, then there is also a file object
for that same digest. However, if there is a file object for a given digest,
there *typically* is an info object for that digest. In the uncommon and
temporary situation that there isn't, the client should retry checking both the
file object and the info object at a later time, at which point both will either
exist or not exist. Alternatively, clients can avoid this situation by always
checking for the info object first.


File retrieval procedure
========================

A file can be retrieved from the mirror using the S3 REST API, given a certain
digest, i.e., content hash of the file. There is only a limited set of digest
types through which a file is accessible in the mirror: at most it will be
``sha256``, ``sha1`` and ``md5``, but at least one of those. One of these digest
types, the *primary* one, is used in the key of the file object, and there may
or may not be alias objects for the other two.

Digests of a file can be looked up in the Azul REST API, using the file's name
or a combination of other metadata properties associated with the file. The Azul
response indicates a file's primary type of digest. If the mirror doesn't
contain a file object for the primary digest returned by Azul, it won't contain
aliases for other digests returned by Azul either, but if Azul returns a primary
digest for a file, the mirror will eventually include aliases for every other
digest returned by Azul for that file.

There are two retrieval procedures, depending on whether the content type of the
file is desired or not, and if the digest is guaranteed to be correct.


Retrieval of just the file content
++++++++++++++++++++++++++++++++++

This method is slightly simpler than the one described in the next section but
it should only be used if the file's content type is not needed, and if it is
acceptable that, in rare circumstances, the file's actual content doesn't match
the digest used in the file object's key or in the key of one of its aliases.

Step 1: Try the file object
---------------------------

Using the digest, compose the key of the file object. Attempt to retrieve the
file object. If the digest originated from Azul and Azul denoted it as primary,
the file object will exist. If the file object does not exist, continue with
step 2. This can happen if the digest originated from another source or if it is
unknown whether the digest is the primary one.

Step 2: Try an alias
--------------------

Using the digest, compose the key of an alias object. This is the key used in
step 1 but with ``alias/`` at the beginning, instead of ``file/``. Attempt to
retrieve the alias object. If the alias object exists, proceed to step 3. If the
alias object doesn't exist, the mirror doesn't include the file, at least not
under the given type of digest.

Step 3: Retrieve the file object
--------------------------------

Using the digest extracted from the alias object's JSON content, compose the key
of the file object. Retrieve the file object (it will exist).


Retrieval of file content and content type
++++++++++++++++++++++++++++++++++++++++++

This method is slightly more involved than the one described in the previous
section but it yields a file's content type in addition to the content itself,
and it guarantees that the digests used in the file and alias objects' keys
match that content. It is the recommended retrieval procedure.

Step 1: Try the info object
---------------------------

Using the digest, compose the key of the info object. Attempt to retrieve the
info object. If the info object exists, extract the ``content-type`` property
from the info object's JSON content and proceed to step 4. If the info object
does not exist, continue with step 2.

Step 2: Try an alias
--------------------

Using the digest, compose the key of an alias object. This is the key used in
step 1 but with ``alias/`` at the beginning, instead of ``info/``. Retrieve the
alias object. If the alias object exists, proceed to step 3. If the alias object
doesn't exist, the mirror doesn't include the file, at least not under the given
type of digest.

Step 3: Retrieve the info object
--------------------------------

Compose the key of the info object using the digest extracted from the alias
object's JSON content. Retrieve the info object (it will exist), extract the
``content-type`` property from its JSON content and proceed to step 4.

Step 4: Retrieve the file object
--------------------------------

Using the current digest, i.e. the one used in step 1 or step 3, compose the key
of the file object. Retrieve the file object (it will exist).


Rationale
=========

How does the specified layout represent the orginal names of the files stored in
the bucket? It doesn't. Because the mirror bucket is content-addressed, the same
file object could be associated with multiple names. File names are metadata
that can be easily retrieved from Azul, a REST webservice for querying an index
of rich metadata describing file objects, including their name, format and
provenance. Azul also provides a convenient way to retrieve the signed URL of
both the original copy of a file in an upstream data repository, as well as that
of the copy in a mirror bucket. The signed URLs minted by Azul encode the name
of the file, so that common user agents such as a web browser, or utilities like
``curl`` or ``wget`` will save a downloaded file under its original name.

Another question might be why the layout doesn't associate the content type
directly with the S3 object. After all, S3 has a mechanism for associating
arbitrary response headers directly with an object. The reason we don't make use
of that feature is that we want the mirror bucket layout to be highly portable.
This makes it possible to replicate the mirror bucket to virtually any file
system or cloud storage service. While this design decision complicates access
to files in the mirror bucket, we've believe we addressed those complications by
offering the Azul endpoint for minting signed URLs mentioned above. The signed
URLs encode both the content type and the name of a file.
