"""Feature engineering. Pure functions over series -- no I/O, no network.

Every function in this package obeys one contract: the value at index *i* is
computed only from observations at indices <= *i*. That is what makes the
features safe to use in a historical simulation.
"""
