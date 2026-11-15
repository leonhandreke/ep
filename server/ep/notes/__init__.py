"""Notes on entities, the links in them, backlinks, and tags.

- ``ep.notes.links``: the link syntax ``[[type:uuid|Display text]]`` and its parser;
- ``ep.notes.store``: saving a note (links rebuilt from the body), ``annotated`` (an
  entity with a note, a tag or an inbound link is never deleted as a side effect), and
  ``delete_annotations``, which every path that deletes an entity calls;
- ``ep.notes.tags``: tag names, find-or-create, rename/merge, imported tags.
"""
