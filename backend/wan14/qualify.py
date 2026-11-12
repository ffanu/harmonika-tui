from bridge import load, BASE, ROOT

if __name__ == '__main__':
    module = load('qualify', BASE/'qualify.py')
    module.CONTROL = ROOT
    module.wf = load('workflow', ROOT/'guided_workflow.py')
    module.main()
