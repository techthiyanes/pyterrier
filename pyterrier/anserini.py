from .model import coerce_queries_dataframe

from tqdm import tqdm
from .batchretrieve import BatchRetrieveBase

import pandas as pd
import numpy as np
from tqdm import tqdm

anserini_monkey=False
def init_anserini():
    global anserini_monkey
    if anserini_monkey:
        return

    # jnius monkypatching
    import jnius_config
    anserini_found = False
    for j in jnius_config.get_classpath():
        if "anserini" in j:
            anserini_found = True
            break
    assert anserini_found, 'Anserini jar not found: you should start Pyterrier with '\
        + 'pt.init(boot_packages=["io.anserini:anserini:0.9.2:fatjar"])'
    jnius_config.set_classpath = lambda x: x
    anserini_monkey = True

    #this is the Anserini early rank cutoff rule
    from matchpy import Wildcard, ReplacementRule, Pattern
    from .transformer import RankCutoffTransformer, rewrite_rules
    x = Wildcard.dot('x')
    _brAnserini = Wildcard.symbol('_brAnserini', AnseriniBatchRetrieve)

    def set_k(_brAnserini, x):
        _brAnserini.k = int(x.value)
        return _brAnserini

    rewrite_rules.append(ReplacementRule(
            Pattern(RankCutoffTransformer(_brAnserini, x) ),
            set_k
    ))



class AnseriniBatchRetrieve(BatchRetrieveBase):
    def __init__(self, index_location : str, k : int = 1000, wmodel : str ="BM25", **kwargs):
        super().__init__(kwargs)
        self.index_location = index_location
        self.k = k
        init_anserini()
        from pyserini.search import pysearch
        self.searcher = pysearch.SimpleSearcher(index_location)
        self.wmodel = wmodel
        self._setsimilarty(wmodel)

    def _setsimilarty(self, wmodel):
        #commented lines are for anserini > 0.9.2
        if wmodel == "BM25":
            self.searcher.object.setBM25Similarity(0.9, 0.4)
            #self.searcher.object.setBM25(self.searcher.object.bm25_k1, self.searcher.object.bm25_b)
        elif wmodel == "QLD":
            self.searcher.object.setLMDirichletSimilarity(1000.0)
            #self.searcher.object.setQLD(self.searcher.object.ql_mu)
        elif wmodel == "TFIDF":
            from jnius import autoclass
            self.searcher.object.similarty = autoclass("org.apache.lucene.search.similarities.ClassicSimilarity")()
        else:
            raise ValueError("wmodel %s not support in AnseriniBatchRetrieve" % wmodel) 

    def _getsimilarty(self, wmodel):
        from jnius import autoclass
        if wmodel == "BM25":
            return autoclass("org.apache.lucene.search.similarities.BM25Similarity")(0.9, 0.4)#(self.searcher.object.bm25_k1, self.searcher.object.bm25_b)
        elif wmodel == "QLD":
            return autoclass("org.apache.lucene.search.similarities.LMDirichletSimilarity")(1000.0)# (self.searcher.object.ql_mu)
        elif wmodel == "TFIDF":
            return autoclass("org.apache.lucene.search.similarities.ClassicSimilarity")()
        else:
            raise ValueError("wmodel %s not support in AnseriniBatchRetrieve" % wmodel) 

    def __str__(self):
        return "AnseriniBatchRetrieve()"

    def __repr__(self):
        return "AnseriniBatchRetrieve("+self.wmodel + ","+self.k+")"
    
    def transform(self, queries : pd.DataFrame) -> pd.DataFrame:
        """
        Performs the retrieval

        Args:
            queries: String for a single query, list of queries, or a pandas.Dataframe with columns=['qid', 'query']

        Returns:
            pandas.Dataframe with columns=['qid', 'docno', 'rank', 'score']
        """
        results=[]
        if not isinstance(queries, pd.DataFrame):
            queries=coerce_queries_dataframe(queries)
        docno_provided = "docno" in queries.columns
        docid_provided = "docid" in queries.columns
        scores_provided = "scores" in queries.columns
        if docid_provided and not docno_provided:
            raise KeyError("Anserini doesnt expose Lucene's internal docids, you need the docnos")
        if docno_provided: #we are re-ranking
            from . import autoclass
            indexreaderutils = autoclass("io.anserini.index.IndexReaderUtils")
            indexreader = self.searcher.object.reader
            rank = 0
            last_qid = None
            sim = self._getsimilarty(self.wmodel)
            for row in tqdm(queries.itertuples(), desc=self.name, total=queries.shape[0], unit="d") if self.verbose else queries.itertuples():
                qid = str(row.qid)
                query = row.query
                docno = row.docno
                if last_qid is None or last_qid != qid:
                    rank = 0
                rank += 1
                score = indexreaderutils.computeQueryDocumentScore(indexreader, docno, query, sim)
                results.append([qid, query, docno, rank, score])

        else: #we are searching, no candidate set provided
            for index,row in tqdm(queries.itertuples(), desc=self.name, total=queries.shape[0], unit="q") if self.verbose else queries.itertuples():
                rank = 0
                qid = str(row.qid)
                query = row.query
                
                hits = self.searcher.search(query, k=self.k)
                for i in range(0, min(len(hits), self.k)):
                    res = [qid, query,hits[i].docid,rank, hits[i].score]
                    rank += 1
                    results.append(res)   
                
        res_dt = pd.DataFrame(results, columns=['qid', 'query'] + ["docno"] + ['rank', 'score'])
        return res_dt