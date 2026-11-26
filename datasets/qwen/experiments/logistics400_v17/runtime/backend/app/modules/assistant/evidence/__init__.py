"""Evidence-only model output handling.

The model returns role-tagged spans copied from the user's own words.  This
package checks those spans and turns them into backend values.  It never reads
or writes Schedule data and never trusts a model-computed value.
"""
