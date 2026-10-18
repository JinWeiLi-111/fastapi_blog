class A:
    def __enter__(self):
        print("进入with块")
        return self

    def __exit__(self, exc_type, exc, tb):
        print("退出with块")
        return True


if __name__ == "__main__":
    with A() as a:
        print(type(a))
        print("learning with case")
