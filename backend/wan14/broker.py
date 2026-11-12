from bridge import load, BASE, ROOT

if __name__ == '__main__':
    module = load('broker', BASE/'broker.py')
    module.CONTROL = ROOT
    module.wf = load('workflow', ROOT/'guided_workflow.py')
    module.PORT = 18263
    module.main()
