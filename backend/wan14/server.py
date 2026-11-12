from bridge import load, BASE

if __name__ == '__main__':
    module = load('server', BASE/'server.py')
    module.main(root='/var/lib/harmonika-wan14-video', port=18268, worker_base='http://127.0.0.1:18264')
