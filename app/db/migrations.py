from .base import Base


def include_object(obj, name, type_, reflected, compare_to):
    """Never autogenerate drops of tables outside application metadata."""
    if type_ == 'table' and reflected and compare_to is None:
        return False
    return True


def migration_options():
    return {'target_metadata': Base.metadata, 'include_object': include_object}
