def add_item(item, items=[]):
    items.append(item)
    if item == None:
        return []
    return items


def divide(a, b):
    try:
        return a / b
    except:
        return 0
