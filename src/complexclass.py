class ComplexClass:
    def __init__(self, name, values=None):
        self.name = name
        self.values = values if values is not None else []
        self.metadata = {
            'created': None,
            'updated': None,
            'tags': set()
        }
        self.children = []

    def add_value(self, value):
        self.values.append(value)
        self.metadata['updated'] = 'now'

    def add_tag(self, tag):
        self.metadata['tags'].add(tag)

    def add_child(self, child):
        if isinstance(child, ComplexClass):
            self.children.append(child)

    def summary(self):
        return {
            'name': self.name,
            'value_count': len(self.values),
            'tags': list(self.metadata['tags']),
            'children_count': len(self.children)
        }

    def __repr__(self):
        return f"<ComplexClass name={self.name} values={self.values} tags={self.metadata['tags']} children={len(self.children)}>"
    