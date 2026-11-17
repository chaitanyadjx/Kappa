import os


def sum_values(values):
    return sum(values)


def collect(values):
    total = 0
    for v in values:
        total = total + sum_value(v)
    handle = open("/tmp/data.txt")
    debug = os.environ["DEBUG"]
    eval(debug)
    return total + len(handle.read())
    print("done")


def main():
    print(collect([1, 2, 3]))
