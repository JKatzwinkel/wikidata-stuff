#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
Bot to import and sourced statements about entities also present in
KulturNav (https://www.wikidata.org/wiki/Q16323066).

usage:
    python kulturnavBot.py [OPTIONS]

Based on http://git.wikimedia.org/summary/labs%2Ftools%2Fmultichill.git
    /bot/wikidata/rijksmuseum_import.py by Multichill

Author: Lokal_Profil
License: MIT

Options (may be omitted):
  -cutoff:INT       number of entries to process before terminating
  -maxHits:INT      number of items to request at a time from Kulturnav
                    (default 250)

See https://github.com/lokal-profil/wikidata-stuff/issues for TODOs
"""
import json
import pywikibot
from pywikibot import pagegenerators
import urllib2
import pywikibot.data.wikidataquery as wdquery
from WikidataStuff import WikidataStuff

FOO_BAR = u'A multilingual result (or one with multiple options) was ' \
          u'encountered but I have yet to support that functionality'


class KulturnavBot(object):
    """
    A bot to enrich and create information on Wikidata based on KulturNav info
    """
    EDIT_SUMMARY = 'KulturnavBot'
    KULTURNAV_ID_P = '1248'
    DATASET_Q = None
    STATED_IN_P = '248'
    IS_A_P = '31'
    PUBLICATION_P = '577'
    CATALOG_P = '972'
    DATASET_ID = None
    ENTITY_TYPE = None
    MAP_TAG = None

    def __init__(self, dictGenerator):
        """
        Arguments:
            * generator    - A generator that yields Dict objects.
        """
        self.generator = dictGenerator
        self.repo = pywikibot.Site().data_repository()
        self.cutoff = None

        # trigger wdq query
        self.itemIds = self.fillCache()

        # set up WikidataStuff object
        self.wd = WikidataStuff(self.repo)

    @classmethod
    def setVariables(cls, dataset_q, dataset_id, entity_type,
                     map_tag, edit_summary=None):
        cls.DATASET_Q = dataset_q
        cls.DATASET_ID = dataset_id
        cls.ENTITY_TYPE = entity_type
        cls.MAP_TAG = map_tag
        if edit_summary is not None:
            cls.EDIT_SUMMARY = edit_summary

    def fillCache(self, queryoverride=u'', cacheMaxAge=0):
        """
        Query Wikidata to fill the cache of entities which have an object
        """
        result = {}
        if queryoverride:
            query = queryoverride
        else:
            query = u'CLAIM[%s]' % self.KULTURNAV_ID_P
        wd_queryset = wdquery.QuerySet(query)

        wd_query = wdquery.WikidataQuery(cacheMaxAge=cacheMaxAge)
        data = wd_query.query(wd_queryset, props=[str(self.KULTURNAV_ID_P), ])

        if data.get('status').get('error') == 'OK':
            expectedItems = data.get('status').get('items')
            props = data.get('props').get(str(self.KULTURNAV_ID_P))
            for prop in props:
                # FIXME: This will overwrite id's that are used more than once.
                # Use with care and clean up your dataset first
                result[prop[2]] = prop[0]

            if expectedItems == len(result):
                pywikibot.output('I now have %s items in cache' %
                                 expectedItems)

        return result

    def run(self):
        """
        Starts the robot
        """
        raise NotImplementedError("Please Implement this method")

    def runLayout(self, datasetValues, datasetProtoclaims,
                  datasetSanityTest, label, shuffle):
        """
        The basic layout of a run. It should be called for a dataset
        specific run which sets the parameters.

        param datasetValues: a dict of additional values to look for
        param datasetProtoclaims: a function for populating protoclaims
        param datasetSanityTest: a function which must return true for
                                 results to be written to Wikidata
        param label: the key in values to be used for label/alias.
                     set to None to skip addNames()
        param shuffle: whether name/label/alias is shuffled or not
                       i.e. if name = last, first
        """
        count = 0
        for hit in self.generator:
            # print count, cutoff
            if self.cutoff and count >= self.cutoff:
                break
            # Required values to searching for
            values = {u'identifier': None,
                      u'modified': None,
                      u'seeAlso': None,
                      u'sameAs': None,
                      # not expected
                      u'wikidata': None,
                      u'libris-id': None,
                      u'viaf-id': None}
            values.update(datasetValues)

            # populate values
            if not self.populateValues(values, hit):
                # continue with next hit if problem was encounterd
                continue

            # find the matching wikidata item
            hitItem = self.wikidataMatch(values)

            # convert values to potential claims
            protoclaims = datasetProtoclaims(self, values)

            protoclaims[u'P%s' % self.KULTURNAV_ID_P] = values[u'identifier']
            if values[u'libris-id']:
                protoclaims[u'P906'] = values[u'libris-id']
            if values[u'viaf-id']:
                protoclaims[u'P214'] = values[u'viaf-id']

            # Add information if a match was found
            if hitItem and hitItem.exists():

                # make sure it passes the sanityTest
                if not datasetSanityTest(self, hitItem):
                    continue

                # add name as label/alias
                if label is not None:
                    self.addNames(values[label], hitItem, shuffle=shuffle)

                # get the "last modified" timestamp
                date = self.dbDate(values[u'modified'])

                # construct a refObject
                ref = self.makeRef(date)

                # add each property (if new) and source it
                self.addProperties(protoclaims, hitItem, ref)

            # allow for limited runs
            count += 1

        # done
        pywikibot.output(u'Handled %d entries' % count)

    def populateValues(self, values, hit):
        """
        given a list of values and a kulturnav hit, populate the values
        and check if result is problem free

        return bool problemFree
        """
        problemFree = True
        for entries in hit[u'@graph']:
            for k, v in entries.iteritems():
                if k in values.keys():
                    if values[k] is None:
                        values[k] = v
                    else:
                        pywikibot.output(u'duplicate entries for %s' % k)
                        problemFree = False

        # the minimum which must have been identified
        if values[u'identifier'] is None:
            pywikibot.output(u'Could not isolate the identifier from the '
                             u'KulturNav object! JSON layout must have '
                             u'changed. Crashing!')
            exit(1)

        # dig into sameAs and seeAlso
        # each can be either a list or a str/unicode
        if isinstance(values[u'sameAs'], (str, unicode)):
            values[u'sameAs'] = [values[u'sameAs'], ]
        if values[u'sameAs'] is not None:
            for sa in values[u'sameAs']:
                if u'wikidata' in sa:
                    values[u'wikidata'] = sa.split('/')[-1]
                elif u'libris-id' in values.keys() and \
                        u'libris.kb.se/auth/' in sa:
                    values[u'libris-id'] = sa.split('/')[-1]
                elif u'viaf-id' in values.keys() and \
                        u'viaf.org/viaf/' in sa:
                    values[u'viaf-id'] = sa.split('/')[-1]
        # we only care about seeAlso if we didn't find a Wikidata link
        if values[u'wikidata'] is None:
            if isinstance(values[u'seeAlso'], (str, unicode)):
                values[u'seeAlso'] = [values[u'seeAlso'], ]
            for sa in values[u'seeAlso']:
                if u'wikipedia' in sa:
                    pywikibot.output(u'Found a Wikipedia link but no '
                                     u'Wikidata link: %s %s' %
                                     (sa, values[u'identifier']))
            problemFree = False

        if not problemFree:
            pywikibot.output(u'Found an issue with %s (%s), skipping' %
                             (values['identifier'], values['wikidata']))
        return problemFree

    def wikidataMatch(self, values):
        """
        Finds the matching wikidata item
        checks Wikidata first, then kulturNav

        return ItemPage|None the matching item
        """
        if values[u'identifier'] in self.itemIds:
            hitItemTitle = u'Q%s' % \
                (self.itemIds.get(values[u'identifier']),)
            if values[u'wikidata'] != hitItemTitle:
                # this may be caused by either being a redirect
                wd = pywikibot.ItemPage(self.repo, values[u'wikidata'])
                wi = pywikibot.ItemPage(self.repo, hitItemTitle)
                if wd.isRedirectPage() and wd.getRedirectTarget() == wi:
                    pass
                elif wi.isRedirectPage() and wi.getRedirectTarget() == wd:
                    pass
                else:
                    pywikibot.output(
                        u'Identifier missmatch (skipping): '
                        u'%s, %s, %s' % (values[u'identifier'],
                                         values[u'wikidata'],
                                         hitItemTitle))
                    return None
        elif values[u'wikidata']:
            hitItemTitle = values[u'wikidata']
        else:
            # no match found
            return None

        # create ItemPage, bypassing any redirect
        hitItem = self.wd.bypassRedirect(
            pywikibot.ItemPage(
                self.repo,
                hitItemTitle))
        # in case of redirect
        values[u'wikidata'] = hitItem.title()

        return hitItem

    def addNames(self, names, hitItem, shuffle=False):
        """
        Given a nameObj or a list of such this prepares them for
        addLabelOrAlias()

        param: shuffle bool if name order is last, first then this
               creates a local rearranged copy
        """
        if names:
            if shuffle:
                namelist = []
                if isinstance(names, dict):
                    s = KulturnavBot.shuffleNames(names)
                    if s is not None:
                        namelist.append(s)
                elif isinstance(names, list):
                    for n in names:
                        s = KulturnavBot.shuffleNames(n)
                        if s is not None:
                            namelist.append(s)
                else:
                    pywikibot.output(u'unexpectedly formatted name'
                                     u'object: %s' % names)
                if len(namelist) > 0:
                    self.addLabelOrAlias(namelist, hitItem)
            else:
                self.addLabelOrAlias(names, hitItem)

    def addProperties(self, protoclaims, hitItem, ref):
        """
        add each property (if new) and source it
        """
        for pcprop, pcvalue in protoclaims.iteritems():
            if pcvalue:
                if isinstance(pcvalue, unicode) and \
                        pcvalue in (u'somevalue', u'novalue'):
                    # special cases
                    self.wd.addNewSpecialClaim(pcprop, pcvalue,
                                               hitItem, ref)
                elif pcprop == u'P%s' % self.KULTURNAV_ID_P:
                    qual = self.makeQual(self.CATALOG_P,
                                         self.DATASET_Q,
                                         force=True)
                    self.wd.addNewClaim(pcprop, pcvalue, hitItem,
                                        ref, qual=qual)
                else:
                    self.wd.addNewClaim(pcprop, pcvalue,
                                        hitItem, ref)

    # KulturNav specific functions
    def dbpedia2Wikidata(self, item):
        """
        Converts a dbpedia reference to the equivalent Wikidata item,
        if present
        param item: dict with @language, @value keys
        """
        if KulturnavBot.foobar(item):
            return
        if not all(x in item.keys() for x in (u'@value', u'@language')):
            print u'invalid dbpedia entry: %s' % item
            exit(1)

        # any site will work, this is just an example
        site = pywikibot.Site(item[u'@language'], 'wikipedia')
        page = pywikibot.Page(site, item[u'@value'])
        if u'wikibase_item' in page.properties() and \
           page.properties()[u'wikibase_item']:
            return pywikibot.ItemPage(
                self.repo,
                page.properties()[u'wikibase_item'])

    def dbDate(self, item):
        """
        Given a dbpprop date object (1922-09-17Z or 2014-07-11T08:14:46Z)
        this returns the equivalent pywikibot.WbTime object
        """
        item = item[:len('YYYY-MM-DD')].split('-')
        if len(item) == 3 and all(self.is_int(x) for x in item):
            # 1921-09-17Z or 2014-07-11T08:14:46Z
            d = int(item[2])
            if d == 0:
                d = None
            m = int(item[1])
            if m == 0:
                m = None
            return pywikibot.WbTime(
                year=int(item[0]),
                month=m,
                day=d)
        elif len(item) == 1 and self.is_int(item[0][:len('YYYY')]):
            # 1921Z
            return pywikibot.WbTime(year=int(item[0][:len('YYYY')]))
        elif len(item) == 2 and \
                all(self.is_int(x) for x in (item[0], item[1][:len('MM')])):
            # 1921-09Z
            m = int(item[1][:len('MM')])
            if m == 0:
                m = None
            return pywikibot.WbTime(
                year=int(item[0]),
                month=m)
        else:
            pywikibot.output(u'invalid dbpprop date entry: %s' % item)
            exit(1)

    def dbGender(self, item):
        """
        Simply matches gender values to Q items
        """
        known = {u'male': u'Q6581097',
                 u'female': u'Q6581072',
                 u'unknown': u'somevalue'}  # a special case
        if item not in known.keys():
            pywikibot.output(u'invalid gender entry: %s' % item)
            return

        if known[item] in (u'somevalue', u'novalue'):
            return known[item]
        else:
            return pywikibot.ItemPage(self.repo, known[item])

    def dbName(self, name, typ):
        """
        Given a plaintext name (first or last) this checks if there is
        a matching object of the right type
        param name = {'@language': 'xx', '@value': 'xxx'}
        """
        if KulturnavBot.foobar(name):
            return
        prop = {u'lastName': (u'Q101352',),
                u'firstName': (u'Q12308941', u'Q11879590', u'Q202444')}

        # Skip any empty values
        if len(name['@value'].strip()) == 0:
            return

        # search for potential matches
        matches = []
        if self.wd.onLabs:
            objgen = pagegenerators.PreloadingItemGenerator(
                self.wd.searchGenerator(
                    name['@value'], name['@language']))
            for obj in objgen:
                if u'P%s' % self.IS_A_P in obj.get().get('claims'):
                    # print 'claims:', obj.get().get('claims')[u'P31']
                    values = obj.get().get('claims')[u'P%s' % self.IS_A_P]
                    for v in values:
                        # print u'val:', v.getTarget()
                        if v.getTarget().title() in prop[typ]:
                            matches.append(obj)
        else:
            objgen = pagegenerators.PreloadingItemGenerator(
                pagegenerators.WikibaseItemGenerator(
                    pagegenerators.SearchPageGenerator(
                        name['@value'], step=None, total=10,
                        namespaces=[0], site=self.repo)))

            # check if P31 and then if any of prop[typ] in P31
            for obj in objgen:
                # print obj.title()
                if name['@value'] in (obj.get().get('labels').get('en'),
                                      obj.get().get('labels').get('sv'),
                                      obj.get().get('aliases').get('en'),
                                      obj.get().get('aliases').get('sv')):
                    # print 'labels en:', obj.get().get('labels').get('en')
                    # print 'labels sv:', obj.get().get('labels').get('sv')
                    # Check if right type of object
                    if u'P%s' % self.IS_A_P in obj.get().get('claims'):
                        # print 'claims:', obj.get().get('claims')[u'P31']
                        values = obj.get().get('claims')[u'P%s' % self.IS_A_P]
                        for v in values:
                            # print u'val:', v.getTarget()
                            if v.getTarget().title() in prop[typ]:
                                matches.append(obj)

        # get rid of duplicates then check for uniqueness
        matches = list(set(matches))
        if len(matches) == 1:
            return matches[0]
        elif len(matches) > 1:
            pywikibot.log(u'Possible duplicates: %s' % matches)

    def kulturnav2Wikidata(self, uuid):
        """
        Given a kulturNav uuid this returns the Wikidata entity connected
        to this uuid through the KULTURNAV_ID_P property (if any).

        NOTE that the WDQ results may be outdated
        return itemPage|None
        """
        if uuid in self.itemIds.keys():
            qNo = u'Q%d' % self.itemIds[uuid]
            return pywikibot.ItemPage(self.repo, qNo)
        else:
            return None

    @staticmethod
    def is_int(s):
        try:
            int(s)
            return True
        except (ValueError, TypeError):
            return False

    @staticmethod
    def shuffleNames(nameObj):
        """
        Detects if a @value string is "Last, First" and if so returns
        it as "First Last".
        Strings without commas are returned as is.
        Strings with multiple commas result in an None being returned.

        param nameObj = {'@language': 'xx', '@value': 'xxx'}
        return nameObj|None
        """
        name = nameObj['@value']
        if name.find(',') > 0 and len(name.split(',')) == 2:
            p = name.split(',')
            name = u'%s %s' % (p[1].strip(), p[0].strip())
            nameObj = nameObj.copy()
            nameObj['@value'] = name
            return nameObj
        elif name.find(',') == -1:
            # no comma means just a nickname e.g. Michelangelo
            return nameObj
        else:
            # e.g. more than 1 comma
            pywikibot.output(u'unexpectedly formatted name: %s' % name)
            return None

    def makeRef(self, date):
        """
        Make a correctly formatted ref object for claims
        """
        ref = {
            'source_P': u'P%s' % self.STATED_IN_P,
            'source': pywikibot.ItemPage(self.repo,
                                         u'Q%s' % self.DATASET_Q),
            'time_P': u'P%s' % self.PUBLICATION_P,
            'time': date,
        }
        return ref

    def makeQual(self, P, Q, force=False):
        """
        Make a correctly formatted qualifier object for claims
        """
        qual = {
            u'prop': u'P%s' % P,
            u'itis': pywikibot.ItemPage(
                self.repo,
                u'Q%s' % Q),
            u'force': force
        }
        return qual

    def addLabelOrAlias(self, nameObj, item):
        """
        Adds a name as either a label (if none) or an alias.
        Essentially a filter for the more generic method in WikidatStuff

        param nameObj = {'@language': 'xx', '@value': 'xxx'}
                        or a list of such
        """
        # for a list of entries
        if isinstance(nameObj, list):
            for n in nameObj:
                self.addLabelOrAlias(n, item)
                # reload item so that next call is aware of any changes
                item = pywikibot.ItemPage(self.repo, item.title())
                item.exists()
            return

        # for a single entry
        self.wd.addLabelOrAlias(nameObj['@language'], nameObj['@value'],
                                item, prefix=self.EDIT_SUMMARY)

    @classmethod
    def getKulturnavGenerator(cls, maxHits=500):
        """
        Generator of the entries at KulturNav based on a search for all items
        of given type in the given dataset which contains Wikidata as a
        given value.
        """
        patterns = (u'http://www.wikidata.org/entity/',
                    u'https://www.wikidata.org/entity/')
        q = '*%2F%2Fwww.wikidata.org%2Fentity%2FQ*'
        searchurl = 'http://kulturnav.org/api/search/' + \
                    'entityType:' + cls.ENTITY_TYPE + ',' + \
                    'entity.dataset_r:' + cls.DATASET_ID + ',' + \
                    cls.MAP_TAG + ':%s/%d/%d'
        queryurl = 'http://kulturnav.org/%s?format=application/ld%%2Bjson'

        # get all id's in KulturNav which link to Wikidata
        wdDict = {}

        offset = 0
        # overviewPage = json.load(urllib2.urlopen(searchurl % (q, offset, maxHits)))
        searchPage = urllib2.urlopen(searchurl % (q, offset, maxHits))
        searchData = searchPage.read()
        overviewPage = json.loads(searchData)

        while len(overviewPage) > 0:
            for o in overviewPage:
                sameAs = o[u'properties'][cls.MAP_TAG[:cls.MAP_TAG.rfind('_')]]
                for s in sameAs:
                    if s[u'value'].startswith(patterns):
                        wdDict[o[u'uuid']] = s[u'value'].split('/')[-1]
                        break
            # continue
            offset += maxHits
            searchPage = urllib2.urlopen(searchurl % (q, offset, maxHits))
            searchData = searchPage.read()
            overviewPage = json.loads(searchData)

        # get the record for each of these entries
        for kulturnavId, wikidataId in wdDict.iteritems():
            # jsonData = json.load(urllib2.urlopen(queryurl % kulturnavId))
            recordPage = urllib2.urlopen(queryurl % kulturnavId)
            recordData = recordPage.read()
            jsonData = json.loads(recordData)
            if jsonData.get(u'@graph'):
                yield jsonData
            else:
                print jsonData

    @classmethod
    def main(cls, *args):
        # handle arguments
        cutoff = None
        maxHits = 250

        def if_arg_value(arg, name):
            if arg.startswith(name):
                yield arg[len(name) + 1:]

        for arg in pywikibot.handle_args(args):
            for v in if_arg_value(arg, '-cutoff'):
                cutoff = int(v)
            for v in if_arg_value(arg, '-maxHits'):
                maxHits = int(v)

        kulturnavGenerator = cls.getKulturnavGenerator(maxHits=maxHits)

        kulturnavBot = cls(kulturnavGenerator)
        kulturnavBot.cutoff = cutoff
        kulturnavBot.run()

    @staticmethod
    def foobar(item):
        if isinstance(item, list):
            pywikibot.output(FOO_BAR)
            return True
        return False

if __name__ == "__main__":
    KulturnavBot.main()
