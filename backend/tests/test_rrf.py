from oncall.rag.retrieval import rrf

def test_rrf_fuses():
    a=[{'id':'1','content':'a'},{'id':'2','content':'b'}];b=[{'id':'2','content':'b'},{'id':'3','content':'c'}]
    out=rrf([a,b]);assert out[0]['id']=='2'
